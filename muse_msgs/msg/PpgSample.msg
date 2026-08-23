# One raw 16-channel Athena optical sample in arbitrary units.
# Channel order: OPT0 through OPT15.
std_msgs/Header header
string operator_id
uint16 sampling_rate_hz
float32[16] data
