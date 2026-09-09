# Excalidraw 0.18.1 font licenses

These notices accompany the Excalidraw font assets distributed by MonkeyBoard.
Eight families retain `@excalidraw/excalidraw@0.18.1`'s original files; Liberation
Sans is replaced with official version 2.1.5 under OFL 1.1. The npm package's
MIT license does not replace these font licenses.

The original 234 installed WOFF2 files across nine folders were compared with
the Git blob identities in the [official Excalidraw v0.18.1 font tree](https://github.com/excalidraw/excalidraw/tree/v0.18.1/packages/excalidraw/fonts).
Every file matched before replacement. The eight unchanged families contain
233 of those files. The remaining file now comes from Liberation Fonts 2.1.5,
as described below; the old Liberation Sans 1.05 is not distributed.
Versions below come from the distributed fonts' name tables.

| Package font folder | Bundled font version | Files | License text and exact source |
| --- | --- | ---: | --- |
| `Assistant` | Version 3.000 | 4 | [OFL 1.1](Assistant/OFL.txt); [official source](https://raw.githubusercontent.com/google/fonts/89c9db01508963eb8b48a171c8baf2ef750c5bd9/ofl/assistant/OFL.txt) |
| `Cascadia` | Version 2005.150 | 1 | [OFL 1.1](Cascadia/OFL.txt); [official source](https://raw.githubusercontent.com/microsoft/cascadia-code/0610f2df4356200adb93cb5bca2221b92ad6ee7e/LICENSE) |
| `ComicShanns` | 1.3.0 | 4 | [MIT](ComicShanns/LICENSE.txt); [official source](https://raw.githubusercontent.com/excalidraw/excalidraw/v0.18.1/packages/excalidraw/fonts/ComicShanns/index.ts) |
| `Excalifont` | Version 1.000; Glyphs 3.2 (3227) | 7 | [OFL 1.1](Excalifont/OFL.txt); [official source](https://raw.githubusercontent.com/excalidraw/excalidraw/v0.18.1/packages/excalidraw/fonts/Excalifont/index.ts) |
| `Liberation` | Version 2.1.5 | 1 | [OFL 1.1](Liberation/OFL.txt); [official release](https://github.com/liberationfonts/liberation-fonts/releases/tag/2.1.5) |
| `Lilita` | Version 1.002 | 2 | [OFL 1.1](Lilita/OFL.txt); [official source](https://raw.githubusercontent.com/google/fonts/90abd17b4f97671435798b6147b698aa9087612f/ofl/lilitaone/OFL.txt) |
| `Nunito` | Version 3.602 | 5 | [OFL 1.1](Nunito/OFL.txt); [official source](https://raw.githubusercontent.com/google/fonts/a5bd0ea86b2576f86672aab557a6024d272187a5/ofl/nunito/OFL.txt) |
| `Virgil` | Version 001.001 | 1 | [OFL 1.1](Virgil/OFL.txt); [official source](https://raw.githubusercontent.com/excalidraw/excalidraw/v0.18.1/packages/excalidraw/fonts/Virgil/Virgil-Regular.woff2) |
| `Xiaolai` | Version 3.11; December 4, 2020; FontCreator 13.0.0.2613 64-bit | 209 | [OFL 1.1](Xiaolai/OFL.txt); [official source](https://raw.githubusercontent.com/lxgw/kose-font/21828241081eb0ecc1b4cb40bd9a1b253c09c488/License.txt) |

## Source and extraction details

- **Assistant:** the Google Fonts OFL notice matches the 2020 Assistant and 2010
  Source Sans Pro copyright statements embedded in these version 3.000 fonts.
  The notice identifies the original [Assistant project](https://github.com/hafontia/Assistant).
- **Cascadia:** the notice is from Microsoft's `v2005.15` commit, corresponding
  to the bundled version `2005.150`. It includes the Reserved Font Name
  `Cascadia Code`.
- **ComicShanns:** `LICENSE.txt` is the complete `copyright` field from the
  official v0.18.1 source file and matches the bundled font's name table. It
  retains all five copyright holders, including the 2024 modifications.
- **Excalifont:** `OFL.txt` preserves the copyright line and the complete
  `license` field from the official v0.18.1 source file. It does not substitute
  Excalidraw's software license for the font license.
- **Liberation:** the distributed font is **Liberation Sans 2.1.5**, replacing
  Excalidraw's old 1.05. `OFL.txt` and [AUTHORS](Liberation/AUTHORS) are unmodified
  originals from the [TTF archive linked by the official release](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).
  The original TTF and the losslessly compressed WOFF2 are retained in
  `apps/archflow-studio/web/assets/board-fonts/`; its README records source,
  hashes and conversion. All font tables and metadata were verified unchanged
  except the WOFF2 `head` checksum/compression flag. No glyph subsetting was
  applied. This font uses its original OFL copyrights and reserved names;
  the old Liberation GPL text is not used to license the replacement.
- **Lilita:** the Google Fonts OFL file retains the upstream Reserved Font Name
  `Lilita`. [COPYRIGHT.txt](Lilita/COPYRIGHT.txt) also preserves the exact bundled
  notice, which spells the reserved name `Lilita One`.
- **Nunito:** the Google Fonts OFL notice matches the bundled 2014 Nunito
  Project Authors notice and identifies the [font's upstream project](https://github.com/googlefonts/nunito).
- **Virgil:** `OFL.txt` is the exact copyright and complete license text
  extracted from name records 0 and 13 of the bundled WOFF2. Its bytes were
  verified against the official v0.18.1 font file linked above.
- **Xiaolai:** the original OFL text is from upstream `lxgw/kose-font` tag
  `v3.110`. [COPYRIGHT.txt](Xiaolai/COPYRIGHT.txt) preserves the bundled
  `Copyright © 2020 LXGW` notice. Excalidraw's [v0.18.1 source](https://github.com/excalidraw/excalidraw/blob/v0.18.1/packages/excalidraw/fonts/Xiaolai/index.ts)
  identifies Xiaolai SC 3.11 and records its subset, Hangul side-bearing and
  centering changes. The upstream project uses the name [Xiaolai Font](https://github.com/lxgw/kose-font).

Files downloaded as license documents are retained byte for byte. Text extracted
from Excalidraw source comments or font name records retains the original text,
with only a final newline and a blank separator between copyright and license.
These are upstream license and copyright notices, not a new license grant by
MonkeyHub.
