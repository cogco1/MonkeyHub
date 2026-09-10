# Liberation Sans 2.1.5 for MonkeyBoard

`LiberationSans-Regular.ttf`, `LICENSE` and `AUTHORS` are unmodified files from
the archive linked by the [official 2.1.5 release](https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5):
[liberation-fonts-ttf-2.1.5.tar.gz](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).
Retrieved 2026-09-09. The font name table reports `Version 2.1.5`; the license
is SIL OFL 1.1 with the original Google/Red Hat copyrights and reserved names.

This WOFF2 replaces Excalidraw 0.18.1's old Liberation Sans 1.05 in the local
`excalidraw/fonts/Liberation/LiberationSans-Regular.woff2` distribution path.
The old 1.05 font is not distributed by MonkeyBoard. Its GPL notice does not
apply to this replacement.

| File | SHA-256 |
| --- | --- |
| Original release archive | `7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0` |
| Original TTF, 410712 bytes | `76d04c18ea243f426b7de1f3ad208e927008f961dc5945e5aad352d0dfde8ee8` |
| WOFF2, 169340 bytes | `53bf7b267c9c7bb4cb69d30877432ede3853b331807421c419858a2ac64205ee` |
| Original LICENSE | `93fed46019c38bbe566b479d22148e2e8a1e85ada614accb0211c37b2c61c19b` |

## Conversion

Converted with the existing fontTools 4.64.0 and Node.js 24.14.0 built-in Brotli
on 2026-09-09. No font subsetting, glyph transforms, renaming or timestamp
updates were applied. Brotli used FONT mode and quality 11. The original
font tables were passed directly to the WOFF2 writer, with no extra WOFF
metadata. This follows the unchanged-data conversion described in
[OFL FAQ 2.2.1](https://openfontlicense.org/ofl-faq/).

The output was decoded and compared to the original: all raw font tables
match, except the `head` checksum and WOFF2 compression flag required by the
format. The character map, glyph metrics, names and original metadata match.

To reproduce from this directory with the same existing tools:

```python
from io import BytesIO
from pathlib import Path
import subprocess
import types
from fontTools.ttLib import TTFont, woff2

def compress(data, mode=2):
    script = '''
const fs = require("node:fs"), z = require("node:zlib");
process.stdout.write(z.brotliCompressSync(fs.readFileSync(0), {params: {
  [z.constants.BROTLI_PARAM_MODE]: Number(process.argv[1]),
  [z.constants.BROTLI_PARAM_QUALITY]: 11
}}));
'''
    return subprocess.run(["node", "-e", script, str(mode)], input=data,
                          capture_output=True, check=True).stdout

woff2.brotli = types.SimpleNamespace(compress=compress, MODE_FONT=2, MODE_TEXT=1)
woff2.haveBrotli = True
original = TTFont("LiberationSans-Regular.ttf", recalcBBoxes=False,
                  recalcTimestamp=False)
output = BytesIO()
writer = woff2.WOFF2Writer(output, len(original.reader.tables),
    sfntVersion=original.sfntVersion,
    flavorData=woff2.WOFF2FlavorData(transformedTables=set()))
for tag in original.reader.tables:
    writer[tag] = original.reader[tag]
writer.close()
Path("LiberationSans-Regular.woff2").write_bytes(output.getvalue())
```

This is a maintenance conversion example, not an application build dependency.
The application copies the retained WOFF2 and license without rerunning it.
