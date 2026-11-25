"""Verify Windows ICO containers and embedded images without Windows shell access."""
import struct
import unittest
from io import BytesIO
from PIL import Image
from dhan_visual.desktop_icons import icon, SIZES


class DesktopIconTests(unittest.TestCase):
    def test_both_icons_are_distinct_and_decode_at_every_size(self):
        terminal, updater = icon(False), icon(True)
        self.assertNotEqual(terminal, updater)
        for blob in (terminal, updater):
            self.assertEqual(struct.unpack_from("<HHH",blob), (0,1,len(SIZES)))
            for i,size in enumerate(SIZES):
                w,h,_,_,planes,bpp,length,offset=struct.unpack_from("<BBBBHHII",blob,6+16*i)
                self.assertEqual((w or 256,h or 256,planes,bpp),(size,size,1,32))
                self.assertLessEqual(offset+length,len(blob))
                with Image.open(BytesIO(blob[offset:offset+length])) as image:
                    self.assertEqual(image.size,(size,size))
                    self.assertEqual(image.mode,"RGBA")
                    self.assertEqual(image.getpixel((0,0))[3],0)
                    self.assertEqual(image.getpixel((size//2,size//2))[3],255)


if __name__=="__main__":
    unittest.main()
