/* Range FFT (see range.h), with CMSIS-DSP. */
#include "range.h"

#include <math.h>
#include <string.h>

#include "arm_math.h"
#include "ibuki.h"

static arm_rfft_fast_instance_f32 rfft;
static float hann[IBUKI_SAMPLES_PER_CHIRP];
static float chirp_buf[IBUKI_SAMPLES_PER_CHIRP];
static float spec_buf[IBUKI_SAMPLES_PER_CHIRP];
static float sums[2U * IBUKI_NUM_BINS]; /* Re, Im of each bin from IBUKI_FIRST_BIN */
static uint32_t frames;

bool range_init(void)
{
    for (uint32_t n = 0U; n < IBUKI_SAMPLES_PER_CHIRP; n++)
    {
        hann[n] = 0.5f * (1.0f - cosf(2.0f * PI * (float)n / (float)(IBUKI_SAMPLES_PER_CHIRP - 1U)));
    }
    range_clear();
    return arm_rfft_fast_init_f32(&rfft, IBUKI_SAMPLES_PER_CHIRP) == ARM_MATH_SUCCESS;
}

void range_add_frame(const float* frame)
{
    const uint32_t stride = IBUKI_SAMPLES_PER_CHIRP * IBUKI_RX_ANTENNAS;

    for (uint32_t c = 0U; c < IBUKI_CHIRPS_PER_FRAME; c++)
    {
        const float* chirp = frame + (c * stride) + IBUKI_RX_USED;

        /* this antenna's samples sit every IBUKI_RX_ANTENNAS values: gather them first, so
         * the steps below run on one contiguous block */
        for (uint32_t s = 0U; s < IBUKI_SAMPLES_PER_CHIRP; s++)
        {
            chirp_buf[s] = chirp[s * IBUKI_RX_ANTENNAS];
        }
        float mean;
        arm_mean_f32(chirp_buf, IBUKI_SAMPLES_PER_CHIRP, &mean);
        arm_offset_f32(chirp_buf, -mean, chirp_buf, IBUKI_SAMPLES_PER_CHIRP); /* remove DC */
        arm_mult_f32(chirp_buf, hann, chirp_buf, IBUKI_SAMPLES_PER_CHIRP);    /* Hann window */

        /* Output packing: [Re0, Re(N/2), Re1, Im1, Re2, Im2, ...]. arm_rfft_fast_f32 uses
         * chirp_buf as scratch, which is fine because it is refilled for every chirp. */
        arm_rfft_fast_f32(&rfft, chirp_buf, spec_buf, 0U);

        /* bins from IBUKI_FIRST_BIN (>= 1) are plain Re, Im pairs, in the same order as sums */
        arm_add_f32(sums, &spec_buf[2U * IBUKI_FIRST_BIN], sums, 2U * IBUKI_NUM_BINS);
    }
    frames++;
}

uint32_t range_frames(void)
{
    return frames;
}

const float* range_sums(void)
{
    return sums;
}

void range_clear(void)
{
    memset(sums, 0, sizeof(sums));
    frames = 0U;
}
