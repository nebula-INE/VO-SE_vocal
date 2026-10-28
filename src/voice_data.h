#pragma once
#include <stdint.h>

extern "C" void load_embedded_resource(const char* phoneme, const int16_t* raw_data, int sample_count);

inline void register_all_embedded_voices() {}
