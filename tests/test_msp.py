"""MSP v1 framing."""
import struct
import unittest

from fake_fc import FakeMspDevice
from fc_serial import MSP_RC, msp_request


class Framing(unittest.TestCase):
    def test_reads_a_well_formed_reply(self):
        device = FakeMspDevice({MSP_RC: struct.pack("<4H", 1500, 1500, 1500, 1000)})
        payload, error = msp_request(device, MSP_RC)
        self.assertIsNone(error)
        self.assertEqual(struct.unpack("<4H", payload), (1500, 1500, 1500, 1000))

    def test_bad_checksum_is_reported_not_returned(self):
        device = FakeMspDevice({MSP_RC: b"\x00\x01"}, corrupt_checksum=True)
        payload, error = msp_request(device, MSP_RC)
        self.assertIsNone(payload)
        self.assertIn("checksum", error)

    def test_rejection_frame_is_reported(self):
        device = FakeMspDevice({MSP_RC: b""}, send_error=True)
        payload, error = msp_request(device, MSP_RC)
        self.assertIsNone(payload)
        self.assertIn("rejected", error)

    def test_silence_times_out(self):
        class Silent(FakeMspDevice):
            def write(self, data):
                return len(data)

        payload, error = msp_request(Silent(), MSP_RC, timeout=0.2)
        self.assertIsNone(payload)
        self.assertIn("timeout", error)


if __name__ == "__main__":
    unittest.main()
