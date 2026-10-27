/* The two trained models on the board's AI chip (Ethos-U55), and the alarm they drive.
 *
 * Every 8 D lines (~0.5 s), once 12 s of lines are there: the breathing model answers "is
 * something breathing here?" for all 24 distances, the state model reads the last 30 s of those
 * answers and says breathing / stopped / moving / empty, and the alarm rule (the same as the
 * app's, pc/ibuki/model_brain.py) decides. The board then sends
 *     Y,t_ms,<breathing|stopped|moving|empty>,<-|no_breath|left_bed>
 * and on an alarm blinks its LED by itself, with or without the laptop. SW1 acknowledges it.
 *
 * The models come from ml/board_models.py (folder models/, not in git). Without them the
 * firmware builds and runs as before, without Y lines. */
#ifndef IBUKI_AI_H
#define IBUKI_AI_H

void ai_init(void);                  /* the NPU and both models; reports an S line */
void ai_add_line(const float* sums); /* one D line: Re, Im of the 24 bins, in value units */
void ai_acknowledge(void);           /* SW1 was pressed: stop counting and snooze, like the app */

#endif /* IBUKI_AI_H */
