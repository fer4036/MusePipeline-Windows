# One 4-channel Athena EEG sample in microvolts.
# Channel order: TP9, AF7, AF8, TP10.
std_msgs/Header header
string operator_id
uint16 sampling_rate_hz
float32[4] data
