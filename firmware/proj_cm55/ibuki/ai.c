/* The trained models on the Ethos-U55 (see ai.h). The maths follows ml/features.py and
 * ml/state_model.py step by step, so the board gives the app's answers. */
#include "ai.h"

#include <math.h>
#include <string.h>

#include "board_io.h"
#include "cybsp.h"
#include "ibuki.h"
#include "lines.h"
#include "load.h"

#if __has_include("models/ibuki_models.h")
#include "models/ibuki_models.h"
#include "mtb_ml.h"
#include "mtb_ml_model.h"
#define AI_MODELS 1
#else
#define AI_MODELS 0
#endif

#define NB          IBUKI_NUM_BINS
#define WINDOW      (200U)  /* lines in the breathing model's window: 12 s */
#define DECIMATE    (2U)    /* pairs of lines averaged: 100 steps */
#define STEPS       (WINDOW / DECIMATE)
#define NEIGHBOURS  (2U)    /* slots on each side */
#define CHANNELS    (3U * (2U * NEIGHBOURS + 1U)) /* wave, level, amount for 5 slots */
#define HOP         (8U)    /* lines between model runs: ~0.5 s */
#define SEQ         (60U)   /* state model: the last 60 runs, ~30 s */
#define FEATURES    (4U)    /* per slot and run: P(breathing), movement, change, echo level */
#define LAST3       (50U)   /* lines in "the last 3 s" */
#define CHANGES     (17U)   /* lines in "the last 1 s" */
#define LAG_S       (6.0f)  /* the count starts 6 s back (chosen on validation, ml/state_model.py) */
#define LEFT_S      (5.0f)  /* "empty" this long after someone was there: left_bed */
#define NO_BREATH_S (10.0f) /* the alarm setting, the app's default (IBUKI_NO_BREATH_S) */
#define SNOOZE_S    (30.0f) /* after SW1, as the app's Brain */

/* the big arrays (~120 KB) live in SoC memory, like the radar buffers in main.c: the M55's own
 * RAM is full */
#define AI_SOCMEM __attribute__((section(".cy_socmem_data")))


#if AI_MODELS /* everything below is only there when the models are built in */
enum { BREATHING, STOPPED, MOVING, EMPTY };
static const char* const state_names[] = {"breathing", "stopped", "moving", "empty"};
/* the last WINDOW lines, oldest at `head` once full */
AI_SOCMEM static float lines_re[WINDOW][NB];
AI_SOCMEM static float lines_im[WINDOW][NB];
static uint32_t head;
static uint32_t count;
static float last_abs[NB];
AI_SOCMEM static float changes[CHANGES][NB];          /* echo-strength change per line, the last 1 s */
AI_SOCMEM static float steps[SEQ][NB][FEATURES];      /* the state model's inputs, the last 30 s */
static uint32_t steps_n;

/* per run: every line's movement along its main direction (wave), its size (mag), and
 * log10(1 + mag) / 3 (amount), for all slots */
AI_SOCMEM static float wave[WINDOW][NB];
AI_SOCMEM static float mag[WINDOW][NB];
AI_SOCMEM static float amount[WINDOW][NB];

/* the alarm */
static float stopped_since;
static float empty_since;
static bool stopped_counting;
static bool empty_counting;
static bool present;
static bool alerted;
static float snooze_until;

#define BREATH_ARENA (32U * 1024U)
#define STATE_ARENA  (96U * 1024U)
__attribute__((section(".cy_socmem_data"), aligned(16))) static uint8_t breath_arena[BREATH_ARENA];
__attribute__((section(".cy_socmem_data"), aligned(16))) static uint8_t state_arena[STATE_ARENA];
static const mtb_ml_model_bin_t breath_bin = {.name = "breath_slot", .model_bin = ibuki_breath_model,
                                              .model_size = sizeof(ibuki_breath_model), .arena_size = BREATH_ARENA};
static const mtb_ml_model_bin_t state_bin = {.name = "state", .model_bin = ibuki_state_model,
                                             .model_size = sizeof(ibuki_state_model), .arena_size = STATE_ARENA};
static mtb_ml_model_t* breath_model;
static mtb_ml_model_t* state_model;
static bool ready;

typedef struct
{
    MTB_ML_DATA_T* data;
    int zero;
    float scale;
} tensor_t;

static tensor_t breath_in, breath_out, state_in, state_out;

static bool detail(mtb_ml_model_t* m, bool input, tensor_t* t)
{
    size_t size;
    int* dims;
    int dims_len;
    cy_rslt_t r = input ? mtb_ml_model_get_input_detail(m, 0, &t->data, &size, &dims, &dims_len, &t->zero, &t->scale)
                        : mtb_ml_model_get_output_detail(m, 0, &t->data, &size, &dims, &dims_len, &t->zero, &t->scale);
    return r == CY_RSLT_SUCCESS;
}

static MTB_ML_DATA_T quantize(float x, const tensor_t* t)
{
    long q = lrintf(x / t->scale) + t->zero;
    return (MTB_ML_DATA_T)((q > 127L) ? 127L : ((q < -128L) ? -128L : q));
}
#endif

void ai_init(void)
{
#if AI_MODELS
    memset(lines_re, 0, sizeof(lines_re));
    memset(lines_im, 0, sizeof(lines_im));
    head = 0U;
    count = 0U;
    steps_n = 0U;
    present = false;
    alerted = false;
    stopped_counting = false;
    empty_counting = false;
    snooze_until = 0.0f;
    mtb_ml_model_buffer_t bb = {.tensor_arena = breath_arena, .tensor_arena_size = BREATH_ARENA};
    mtb_ml_model_buffer_t sb = {.tensor_arena = state_arena, .tensor_arena_size = STATE_ARENA};
    cy_rslt_t r = mtb_ml_init(3U);
    if (r == CY_RSLT_SUCCESS) { r = mtb_ml_model_init(&breath_bin, &bb, &breath_model); }
    if (r == CY_RSLT_SUCCESS) { r = mtb_ml_model_init(&state_bin, &sb, &state_model); }
    ready = (r == CY_RSLT_SUCCESS) && detail(breath_model, true, &breath_in) && detail(breath_model, false, &breath_out)
            && detail(state_model, true, &state_in) && detail(state_model, false, &state_out);
    if (ready)
    {
        ibuki_status("ai ready: 2 models on the Ethos-U55");
    }
    else
    {
        ibuki_status_code("ai init failed", (uint32_t)r);
    }
#else
    ibuki_status("ai off: no models built in (ml/board_models.py)");
#endif
}

void ai_acknowledge(void)
{
#if AI_MODELS
    alerted = false;
    stopped_counting = false;
    snooze_until = (float)ibuki_millis() / 1000.0f + SNOOZE_S;
#endif
}

#if AI_MODELS
/* line i of the window, 0 = the oldest */
static uint32_t at(uint32_t i)
{
    return (head + i) % WINDOW;
}

/* wave, mag and amount of every line in the window (ml/features.py, window_features) */
static void window_parts(void)
{
    for (uint32_t k = 0U; k < NB; k++)
    {
        float mr = 0.0f;
        float mi = 0.0f;
        for (uint32_t i = 0U; i < WINDOW; i++)
        {
            mr += lines_re[i][k];
            mi += lines_im[i][k];
        }
        mr /= (float)WINDOW;
        mi /= (float)WINDOW;
        /* the line the samples move along: half the angle of the sum of squares */
        float sr = 0.0f;
        float si = 0.0f;
        for (uint32_t i = 0U; i < WINDOW; i++)
        {
            float cr = lines_re[i][k] - mr;
            float ci = lines_im[i][k] - mi;
            sr += (cr * cr) - (ci * ci);
            si += 2.0f * cr * ci;
        }
        float axis = 0.5f * atan2f(si, sr);
        float ca = cosf(axis);
        float sa = sinf(axis);
        for (uint32_t t = 0U; t < WINDOW; t++)
        {
            uint32_t i = at(t);
            float cr = lines_re[i][k] - mr;
            float ci = lines_im[i][k] - mi;
            wave[t][k] = (cr * ca) + (ci * sa);
            mag[t][k] = sqrtf((cr * cr) + (ci * ci));
            amount[t][k] = log10f(1.0f + mag[t][k]) / 3.0f;
        }
    }
}

/* the breathing model's P(breathing) for all 24 slots */
static void breathing(float* p)
{
    float mean_mag[NB];
    for (uint32_t k = 0U; k < NB; k++)
    {
        float s = 0.0f;
        for (uint32_t t = 0U; t < WINDOW; t++)
        {
            s += mag[t][k];
        }
        mean_mag[k] = s / (float)WINDOW;
    }
    for (uint32_t k = 0U; k < NB; k++)
    {
        /* the strongest of the 5 slots, on average (outside the 24: an empty slot) */
        float ref = 1e-3f;
        for (uint32_t j = 0U; j <= 2U * NEIGHBOURS; j++)
        {
            int kk = (int)k + (int)j - (int)NEIGHBOURS;
            if ((kk >= 0) && (kk < (int)NB) && (mean_mag[kk] > ref))
            {
                ref = mean_mag[kk];
            }
        }
        for (uint32_t s = 0U; s < STEPS; s++)
        {
            for (uint32_t j = 0U; j <= 2U * NEIGHBOURS; j++)
            {
                int kk = (int)k + (int)j - (int)NEIGHBOURS;
                float w = 0.0f;
                float lv = 0.0f;
                float am = 0.0f;
                for (uint32_t d = 0U; d < DECIMATE; d++) /* the average of each pair of lines */
                {
                    uint32_t t = (s * DECIMATE) + d;
                    float m = (kk >= 0 && kk < (int)NB) ? mag[t][kk] : 0.0f;
                    w += (kk >= 0 && kk < (int)NB) ? wave[t][kk] / ref : 0.0f;
                    lv += log10f((m / ref) + 1e-3f);
                    am += (kk >= 0 && kk < (int)NB) ? amount[t][kk] : 0.0f;
                }
                MTB_ML_DATA_T* in = &breath_in.data[(s * CHANNELS) + (3U * j)];
                in[0] = quantize(w / (float)DECIMATE, &breath_in);
                in[1] = quantize(lv / (float)DECIMATE, &breath_in);
                in[2] = quantize(am / (float)DECIMATE, &breath_in);
            }
        }
        mtb_ml_model_invoke(breath_model);
        p[k] = ((float)breath_out.data[0] - (float)breath_out.zero) * breath_out.scale;
    }
}

/* the alarm rule, then the Y line (ml/state_model.py, Alarm) */
static void decide(int s, float t)
{
    const char* event = "-";
    if (s == EMPTY)
    {
        if (!empty_counting)
        {
            empty_counting = true;
            empty_since = t;
        }
        if (present && ((t - empty_since) >= LEFT_S))
        {
            event = "left_bed";
            present = false;
        }
    }
    else
    {
        empty_counting = false;
        present = true;
    }
    if (s != STOPPED)
    {
        stopped_counting = false;
        alerted = false;
    }
    else
    {
        if (!stopped_counting)
        {
            stopped_counting = true;
            stopped_since = t - LAG_S;
        }
        if (!alerted && (t >= snooze_until) && ((t - stopped_since) >= NO_BREATH_S))
        {
            alerted = true;
            event = "no_breath";
        }
    }
    if (event[0] != '-')
    {
        board_io_alarm(); /* the LED blinks, with or without the laptop */
    }
    char* p = line_start();
    *p++ = 'Y';
    *p++ = ',';
    p = line_put_u32(p, ibuki_millis());
    *p++ = ',';
    p = line_put_text(p, state_names[s]);
    *p++ = ',';
    p = line_put_text(p, event);
    line_send(p);
}

static void run(void)
{
    uint32_t t0 = load_cycles();
    float p[NB];
    window_parts();
    breathing(p);

    /* movement, change and echo level per slot (ml/state_model.py, steps) */
    float* x = &steps[steps_n % SEQ][0][0];
    for (uint32_t k = 0U; k < NB; k++)
    {
        float mr = 0.0f;
        float mi = 0.0f;
        for (uint32_t t = WINDOW - LAST3; t < WINDOW; t++)
        {
            mr += lines_re[at(t)][k];
            mi += lines_im[at(t)][k];
        }
        mr /= (float)LAST3;
        mi /= (float)LAST3;
        float move = 0.0f;
        for (uint32_t t = WINDOW - LAST3; t < WINDOW; t++)
        {
            float dr = lines_re[at(t)][k] - mr;
            float di = lines_im[at(t)][k] - mi;
            move += sqrtf((dr * dr) + (di * di));
        }
        move /= (float)LAST3;
        float chg = 0.0f;
        for (uint32_t c = 0U; c < CHANGES; c++)
        {
            chg += changes[c][k];
        }
        chg /= (float)CHANGES;
        float level = sqrtf((mr * mr) + (mi * mi));
        x[(k * FEATURES) + 0U] = p[k];
        x[(k * FEATURES) + 1U] = log10f(1.0f + move) / 3.0f;
        x[(k * FEATURES) + 2U] = log10f(1.0f + chg) / 2.0f;
        x[(k * FEATURES) + 3U] = log10f(1.0f + level) / 3.0f;
    }
    steps_n++;

    /* the last SEQ runs, oldest first; zeros before there were that many */
    uint32_t have = (steps_n < SEQ) ? steps_n : SEQ;
    uint32_t n = 0U;
    for (uint32_t r = 0U; r < SEQ; r++)
    {
        bool real = r >= (SEQ - have);
        const float* src = real ? &steps[(steps_n - SEQ + r) % SEQ][0][0] : NULL;
        for (uint32_t i = 0U; i < NB * FEATURES; i++)
        {
            state_in.data[n++] = quantize(real ? src[i] : 0.0f, &state_in);
        }
    }
    mtb_ml_model_invoke(state_model);
    int best = 0;
    for (int s = 1; s < 4; s++)
    {
        if (state_out.data[s] > state_out.data[best])
        {
            best = s;
        }
    }
    load_add(LOAD_AI, t0);
    decide(best, (float)ibuki_millis() / 1000.0f);
}
#endif

void ai_add_line(const float* sums)
{
#if AI_MODELS
    if (!ready)
    {
        return;
    }
    uint32_t i = head;
    for (uint32_t k = 0U; k < NB; k++)
    {
        float re = sums[2U * k];
        float im = sums[(2U * k) + 1U];
        float a = sqrtf((re * re) + (im * im));
        changes[count % CHANGES][k] = (count > 0U) ? fabsf(a - last_abs[k]) : 0.0f;
        last_abs[k] = a;
        lines_re[i][k] = re;
        lines_im[i][k] = im;
    }
    head = (head + 1U) % WINDOW;
    count++;
    if ((count >= WINDOW) && ((count % HOP) == 0U))
    {
        run();
    }
#else
    (void)sums;
#endif
}
