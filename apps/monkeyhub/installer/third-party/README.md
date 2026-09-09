# 随包第三方许可补充

这些原文补充 MonkeyHub Windows x64 候选包所用的 `cadquery-ocp==7.9.3.1.1`
和 `vtk==9.6.2` wheel，以及前端分发依赖的原文缺漏；下表逐份保留来源。
安装包仍须原样保留 Python 的 `LICENSE.txt`、所有 wheel 的 `.dist-info`、包内
LICENSE/NOTICE 及附带 DLL。本目录不替代那些文件。

前端补充原文：[fflate 0.8.2](web-supplement/fflate-0.8.2-LICENSE.txt)、
[rhino3dm 8.32.2](web-supplement/rhino3dm-8.32.2-LICENSE.txt)、
[TinyEXR/OpenEXR 通知](web-supplement/three-0.185.1-EXRLoader-NOTICES.txt)。
精确来源及对应关系见[说明](web-supplement/README.md)。构建时另从各前端锁定安装中
保留 React、ReactDOM、scheduler、three 和 PDF.js 的原始 LICENSE，并在包内列明版本。

## 库与对应源码

- OCCT 7.9.3：[源码](https://github.com/Open-Cascade-SAS/OCCT/tree/V7_9_3)。
  OCP 的[固定构建配置](https://github.com/CadQuery/ocp-build-system/tree/v7.9.3.1.1)
  使用该版本及[构建补丁](https://github.com/CadQuery/ocp-build-system/blob/v7.9.3.1.1/patches/occt-7.9.3/switch-vtk-freetype-cmake-order.patch)。
  Windows 构建另向 `src/TKIVtk/EXTERNLIB` 增加 VTK 链接项，见该版本的
  [build-sdks action](https://github.com/CadQuery/ocp-build-system/blob/v7.9.3.1.1/.github/actions/build-sdks/action.yml)。
  附 LGPL 2.1 及 OCCT 附加例外；这些修改来自上游 wheel 构建。
- VTK 9.6.2：[源码与第三方目录](https://github.com/Kitware/VTK/tree/v9.6.2)。
  附根版权文本、ThirdParty 许可及相关模块通知；第三方目录内的替代许可保留原文，
  不表示 MonkeyHub 自身改用这些许可。
- FreeImage：上述 OCP 构建指定 `freeimage=3.18.*`；
  [原始 FreeImage 3.18.0 源码](https://downloads.sourceforge.net/freeimage/FreeImage3180.zip)。
  本包保留 FreeImage Public License 1.0。
  This software uses the FreeImage open source image library. See
  [the FreeImage project](https://freeimage.sourceforge.io/) for details.
- FreeType：上述构建指定 `freetype=2.12.*`。附
  [2.12.1 源码](https://github.com/freetype/freetype/tree/VER-2-12-1)中的原始许可、
  FTL、GPLv2 及 BDF/PCF 通知；wheel 未给出精确 patch/build 身份。
  Portions of this software are copyright The FreeType Project
  ([freetype.org](https://freetype.org/)). All rights reserved. 本候选采用 FreeType License 分支。
- LibRaw 0.21.5：[源码](https://github.com/LibRaw/LibRaw/tree/0.21.5)。
  OpenEXR 3.4.12：[源码](https://github.com/AcademySoftwareFoundation/openexr/tree/v3.4.12)。
  libpng 1.6.58：[源码](https://github.com/pnggroup/libpng/tree/v1.6.58)。
  OpenJPEG 2.5.4：[源码](https://github.com/uclouvain/openjpeg/tree/v2.5.4)。
  libtiff 4.7.1：[源码](https://gitlab.com/libtiff/libtiff/-/tree/v4.7.1)。
  zlib 1.3.2：[源码](https://github.com/madler/zlib/tree/v1.3.2)。
  Zstandard 1.5.7：[源码](https://github.com/facebook/zstd/tree/v1.5.7)。
  这些版本字符串已在 `cadquery_ocp-7.9.3.1.1-cp313-cp313-win_amd64.whl`
  的对应 DLL 内只读查得；版本字符串不提供 conda 构建号或所有下游补丁。
- 本包使用的 JPEG 库包含 Independent JPEG Group 的成果：
  This software is based in part on the work of the Independent JPEG Group.
  参见下表 libjpeg-turbo 的原始许可证与 IJG README。

## 仍未确定的具体项目

`OCP-native/reference-snapshots/` 保留以下已随 OCP wheel 带入的库的固定上游
许可快照：libdeflate、Imath、libjpeg-turbo、Little CMS、Lerc、XZ/liblzma、
libwebp（含 libsharpyuv/libwebpmux）与 OpenJPH。固定构建配置没有锁定它们的
精确版本；原 DLL 检查仅确认 Imath 的 3.2 ABI 命名。下表的提交明确标识所附
许可原文的来源，**不表示已确认其与各 DLL 的精确源码版本一致**。

OCP wheel 还保留原上游带入的 Microsoft `msvcp140`/`vcomp140` 文件；
[Microsoft 的分发说明](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)
适用于这些文件。本轮没有另外收集或复制开发机的运行库。

此目录补充了已发现的原文缺漏，并给出对应源码位置；尚未取得 OCP 全部传递 DLL
的精确 conda 构建清单与补丁对应关系，也未完成整包的对应源码提供核对。
因此本候选不能声明“所有附带二进制的许可与对应源码已全部核齐”。

## 原文与来源

| 本地文件 | 来源 URL |
| --- | --- |
| [FreeImage-3.18/FIPL-1.0.txt](FreeImage-3.18/FIPL-1.0.txt) | [原始文本](https://freeimage.sourceforge.io/freeimage-license.txt) |
| [FreeType-2.12.1/LICENSE.TXT](FreeType-2.12.1/LICENSE.TXT) | [原始文本](https://raw.githubusercontent.com/freetype/freetype/e8ebfe988b5f57bfb9a3ecb13c70d9791bce9ecf/LICENSE.TXT) |
| [FreeType-2.12.1/docs/FTL.TXT](FreeType-2.12.1/docs/FTL.TXT) | [原始文本](https://raw.githubusercontent.com/freetype/freetype/e8ebfe988b5f57bfb9a3ecb13c70d9791bce9ecf/docs/FTL.TXT) |
| [FreeType-2.12.1/docs/GPLv2.TXT](FreeType-2.12.1/docs/GPLv2.TXT) | [原始文本](https://raw.githubusercontent.com/freetype/freetype/e8ebfe988b5f57bfb9a3ecb13c70d9791bce9ecf/docs/GPLv2.TXT) |
| [FreeType-2.12.1/src/bdf/README](FreeType-2.12.1/src/bdf/README) | [原始文本](https://raw.githubusercontent.com/freetype/freetype/e8ebfe988b5f57bfb9a3ecb13c70d9791bce9ecf/src/bdf/README) |
| [FreeType-2.12.1/src/pcf/README](FreeType-2.12.1/src/pcf/README) | [原始文本](https://raw.githubusercontent.com/freetype/freetype/e8ebfe988b5f57bfb9a3ecb13c70d9791bce9ecf/src/pcf/README) |
| [OCCT-7.9.3/LICENSE_LGPL_21.txt](OCCT-7.9.3/LICENSE_LGPL_21.txt) | [原始文本](https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/a016080bf6738d6aeae020badee4e888ad1540a5/LICENSE_LGPL_21.txt) |
| [OCCT-7.9.3/OCCT_LGPL_EXCEPTION.txt](OCCT-7.9.3/OCCT_LGPL_EXCEPTION.txt) | [原始文本](https://raw.githubusercontent.com/Open-Cascade-SAS/OCCT/a016080bf6738d6aeae020badee4e888ad1540a5/OCCT_LGPL_EXCEPTION.txt) |
| [OCP-native/LibRaw-0.21.5/COPYRIGHT](OCP-native/LibRaw-0.21.5/COPYRIGHT) | [原始文本](https://raw.githubusercontent.com/LibRaw/LibRaw/e8b172e2197c3d45b35ffa992f6cfd9c49fdc100/COPYRIGHT) |
| [OCP-native/LibRaw-0.21.5/LICENSE.CDDL](OCP-native/LibRaw-0.21.5/LICENSE.CDDL) | [原始文本](https://raw.githubusercontent.com/LibRaw/LibRaw/e8b172e2197c3d45b35ffa992f6cfd9c49fdc100/LICENSE.CDDL) |
| [OCP-native/LibRaw-0.21.5/LICENSE.LGPL](OCP-native/LibRaw-0.21.5/LICENSE.LGPL) | [原始文本](https://raw.githubusercontent.com/LibRaw/LibRaw/e8b172e2197c3d45b35ffa992f6cfd9c49fdc100/LICENSE.LGPL) |
| [OCP-native/OpenEXR-3.4.12/LICENSE.md](OCP-native/OpenEXR-3.4.12/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/AcademySoftwareFoundation/openexr/4d22f773b88673eea92cdc57c2391fe9dceeabd5/LICENSE.md) |
| [OCP-native/OpenEXR-3.4.12/PATENTS](OCP-native/OpenEXR-3.4.12/PATENTS) | [原始文本](https://raw.githubusercontent.com/AcademySoftwareFoundation/openexr/4d22f773b88673eea92cdc57c2391fe9dceeabd5/PATENTS) |
| [OCP-native/OpenJPEG-2.5.4/LICENSE](OCP-native/OpenJPEG-2.5.4/LICENSE) | [原始文本](https://raw.githubusercontent.com/uclouvain/openjpeg/6c4a29b00211eb0430fa0e5e890f1ce5c80f409f/LICENSE) |
| [OCP-native/libpng-1.6.58/LICENSE](OCP-native/libpng-1.6.58/LICENSE) | [原始文本](https://raw.githubusercontent.com/pnggroup/libpng/3061454d980de7d53608f594194cfac722721d2a/LICENSE) |
| [OCP-native/libtiff-4.7.1/LICENSE.md](OCP-native/libtiff-4.7.1/LICENSE.md) | [原始文本](https://gitlab.com/libtiff/libtiff/-/raw/v4.7.1/LICENSE.md) |
| [OCP-native/reference-snapshots/Imath/LICENSE.md](OCP-native/reference-snapshots/Imath/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/AcademySoftwareFoundation/Imath/d0a0b115c51f997924cebcca8067ba78ea1cb68e/LICENSE.md) |
| [OCP-native/reference-snapshots/Lerc/LICENSE](OCP-native/reference-snapshots/Lerc/LICENSE) | [原始文本](https://raw.githubusercontent.com/Esri/lerc/5fb768182b0f178879967f1e43920da0295b837c/LICENSE) |
| [OCP-native/reference-snapshots/Lerc/NOTICE](OCP-native/reference-snapshots/Lerc/NOTICE) | [原始文本](https://raw.githubusercontent.com/Esri/lerc/5fb768182b0f178879967f1e43920da0295b837c/NOTICE) |
| [OCP-native/reference-snapshots/Little-CMS/LICENSE](OCP-native/reference-snapshots/Little-CMS/LICENSE) | [原始文本](https://raw.githubusercontent.com/mm2/Little-CMS/cfb2aac13f3f55624245a71350c45f76c161d723/LICENSE) |
| [OCP-native/reference-snapshots/OpenJPH/LICENSE](OCP-native/reference-snapshots/OpenJPH/LICENSE) | [原始文本](https://raw.githubusercontent.com/aous72/OpenJPH/69f3321cfdae5dd5714b7dcbb153a8562cac361d/LICENSE) |
| [OCP-native/reference-snapshots/XZ/COPYING](OCP-native/reference-snapshots/XZ/COPYING) | [原始文本](https://raw.githubusercontent.com/tukaani-project/xz/9fc6f5cd8774ebef8d4e030f7081fb6984c0dc3f/COPYING) |
| [OCP-native/reference-snapshots/XZ/COPYING.0BSD](OCP-native/reference-snapshots/XZ/COPYING.0BSD) | [原始文本](https://raw.githubusercontent.com/tukaani-project/xz/9fc6f5cd8774ebef8d4e030f7081fb6984c0dc3f/COPYING.0BSD) |
| [OCP-native/reference-snapshots/XZ/COPYING.GPLv2](OCP-native/reference-snapshots/XZ/COPYING.GPLv2) | [原始文本](https://raw.githubusercontent.com/tukaani-project/xz/9fc6f5cd8774ebef8d4e030f7081fb6984c0dc3f/COPYING.GPLv2) |
| [OCP-native/reference-snapshots/XZ/COPYING.GPLv3](OCP-native/reference-snapshots/XZ/COPYING.GPLv3) | [原始文本](https://raw.githubusercontent.com/tukaani-project/xz/9fc6f5cd8774ebef8d4e030f7081fb6984c0dc3f/COPYING.GPLv3) |
| [OCP-native/reference-snapshots/XZ/COPYING.LGPLv2.1](OCP-native/reference-snapshots/XZ/COPYING.LGPLv2.1) | [原始文本](https://raw.githubusercontent.com/tukaani-project/xz/9fc6f5cd8774ebef8d4e030f7081fb6984c0dc3f/COPYING.LGPLv2.1) |
| [OCP-native/reference-snapshots/libdeflate/COPYING](OCP-native/reference-snapshots/libdeflate/COPYING) | [原始文本](https://raw.githubusercontent.com/ebiggers/libdeflate/92e6a0db9fa848d742f9eb286c92afc60f2c3dda/COPYING) |
| [OCP-native/reference-snapshots/libjpeg-turbo/LICENSE.md](OCP-native/reference-snapshots/libjpeg-turbo/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/libjpeg-turbo/libjpeg-turbo/1157d37cfab977f8f7f8344aededdfd951222c89/LICENSE.md) |
| [OCP-native/reference-snapshots/libjpeg-turbo/README.ijg](OCP-native/reference-snapshots/libjpeg-turbo/README.ijg) | [原始文本](https://raw.githubusercontent.com/libjpeg-turbo/libjpeg-turbo/1157d37cfab977f8f7f8344aededdfd951222c89/README.ijg) |
| [OCP-native/reference-snapshots/libwebp/AUTHORS](OCP-native/reference-snapshots/libwebp/AUTHORS) | [原始文本](https://raw.githubusercontent.com/webmproject/libwebp/9c4a699e5aacc1995a27ca3bf1643c8b2378616c/AUTHORS) |
| [OCP-native/reference-snapshots/libwebp/COPYING](OCP-native/reference-snapshots/libwebp/COPYING) | [原始文本](https://raw.githubusercontent.com/webmproject/libwebp/9c4a699e5aacc1995a27ca3bf1643c8b2378616c/COPYING) |
| [OCP-native/reference-snapshots/libwebp/PATENTS](OCP-native/reference-snapshots/libwebp/PATENTS) | [原始文本](https://raw.githubusercontent.com/webmproject/libwebp/9c4a699e5aacc1995a27ca3bf1643c8b2378616c/PATENTS) |
| [OCP-native/zlib-1.3.2/LICENSE](OCP-native/zlib-1.3.2/LICENSE) | [原始文本](https://raw.githubusercontent.com/madler/zlib/da607da739fa6047df13e66a2af6b8bec7c2a498/LICENSE) |
| [OCP-native/zstd-1.5.7/COPYING](OCP-native/zstd-1.5.7/COPYING) | [原始文本](https://raw.githubusercontent.com/facebook/zstd/f8745da6ff1ad1e7bab384bd1f9d742439278e99/COPYING) |
| [OCP-native/zstd-1.5.7/LICENSE](OCP-native/zstd-1.5.7/LICENSE) | [原始文本](https://raw.githubusercontent.com/facebook/zstd/f8745da6ff1ad1e7bab384bd1f9d742439278e99/LICENSE) |
| [VTK-9.6.2/Common/Core/LICENSE](VTK-9.6.2/Common/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Common/Core/LICENSE) |
| [VTK-9.6.2/Common/DataModel/LICENSE](VTK-9.6.2/Common/DataModel/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Common/DataModel/LICENSE) |
| [VTK-9.6.2/Common/ExecutionModel/LICENSE](VTK-9.6.2/Common/ExecutionModel/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Common/ExecutionModel/LICENSE) |
| [VTK-9.6.2/Common/Math/LICENSE](VTK-9.6.2/Common/Math/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Common/Math/LICENSE) |
| [VTK-9.6.2/Copyright.txt](VTK-9.6.2/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Copyright.txt) |
| [VTK-9.6.2/Filters/Core/LICENSE](VTK-9.6.2/Filters/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Core/LICENSE) |
| [VTK-9.6.2/Filters/Extraction/LICENSE](VTK-9.6.2/Filters/Extraction/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Extraction/LICENSE) |
| [VTK-9.6.2/Filters/General/LICENSE](VTK-9.6.2/Filters/General/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/General/LICENSE) |
| [VTK-9.6.2/Filters/Geometry/LICENSE](VTK-9.6.2/Filters/Geometry/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Geometry/LICENSE) |
| [VTK-9.6.2/Filters/Hybrid/LICENSE](VTK-9.6.2/Filters/Hybrid/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Hybrid/LICENSE) |
| [VTK-9.6.2/Filters/Imaging/LICENSE](VTK-9.6.2/Filters/Imaging/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Imaging/LICENSE) |
| [VTK-9.6.2/Filters/ParallelImaging/LICENSE](VTK-9.6.2/Filters/ParallelImaging/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/ParallelImaging/LICENSE) |
| [VTK-9.6.2/Filters/ParallelStatistics/LICENSE](VTK-9.6.2/Filters/ParallelStatistics/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/ParallelStatistics/LICENSE) |
| [VTK-9.6.2/Filters/Sources/LICENSE](VTK-9.6.2/Filters/Sources/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Sources/LICENSE) |
| [VTK-9.6.2/Filters/Statistics/LICENSE](VTK-9.6.2/Filters/Statistics/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Statistics/LICENSE) |
| [VTK-9.6.2/Filters/Verdict/LICENSE](VTK-9.6.2/Filters/Verdict/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Filters/Verdict/LICENSE) |
| [VTK-9.6.2/Geovis/Core/LICENSE](VTK-9.6.2/Geovis/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Geovis/Core/LICENSE) |
| [VTK-9.6.2/IO/Core/LICENSE](VTK-9.6.2/IO/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/Core/LICENSE) |
| [VTK-9.6.2/IO/Export/LICENSE](VTK-9.6.2/IO/Export/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/Export/LICENSE) |
| [VTK-9.6.2/IO/Infovis/LICENSE](VTK-9.6.2/IO/Infovis/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/Infovis/LICENSE) |
| [VTK-9.6.2/IO/LANLX3D/LICENSE](VTK-9.6.2/IO/LANLX3D/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/LANLX3D/LICENSE) |
| [VTK-9.6.2/IO/MINC/Copyright.txt](VTK-9.6.2/IO/MINC/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/MINC/Copyright.txt) |
| [VTK-9.6.2/IO/NetCDF/LICENSE](VTK-9.6.2/IO/NetCDF/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/NetCDF/LICENSE) |
| [VTK-9.6.2/IO/PIO/Copyright.txt](VTK-9.6.2/IO/PIO/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/PIO/Copyright.txt) |
| [VTK-9.6.2/IO/PLY/Copyright.txt](VTK-9.6.2/IO/PLY/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/PLY/Copyright.txt) |
| [VTK-9.6.2/IO/ParallelLSDyna/LICENSE](VTK-9.6.2/IO/ParallelLSDyna/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/ParallelLSDyna/LICENSE) |
| [VTK-9.6.2/IO/SQL/LICENSE](VTK-9.6.2/IO/SQL/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/SQL/LICENSE) |
| [VTK-9.6.2/IO/Xdmf2/LICENSE](VTK-9.6.2/IO/Xdmf2/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/IO/Xdmf2/LICENSE) |
| [VTK-9.6.2/Imaging/Core/LICENSE](VTK-9.6.2/Imaging/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Imaging/Core/LICENSE) |
| [VTK-9.6.2/Infovis/Core/LICENSE](VTK-9.6.2/Infovis/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Infovis/Core/LICENSE) |
| [VTK-9.6.2/Infovis/Layout/LICENSE](VTK-9.6.2/Infovis/Layout/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Infovis/Layout/LICENSE) |
| [VTK-9.6.2/Interaction/Style/LICENSE](VTK-9.6.2/Interaction/Style/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Interaction/Style/LICENSE) |
| [VTK-9.6.2/Interaction/Widgets/LICENSE](VTK-9.6.2/Interaction/Widgets/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Interaction/Widgets/LICENSE) |
| [VTK-9.6.2/Rendering/Core/LICENSE](VTK-9.6.2/Rendering/Core/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/Core/LICENSE) |
| [VTK-9.6.2/Rendering/Label/LICENSE](VTK-9.6.2/Rendering/Label/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/Label/LICENSE) |
| [VTK-9.6.2/Rendering/OpenXR/LICENSE](VTK-9.6.2/Rendering/OpenXR/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/OpenXR/LICENSE) |
| [VTK-9.6.2/Rendering/OpenXRRemoting/LICENSE](VTK-9.6.2/Rendering/OpenXRRemoting/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/OpenXRRemoting/LICENSE) |
| [VTK-9.6.2/Rendering/Parallel/LICENSE](VTK-9.6.2/Rendering/Parallel/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/Parallel/LICENSE) |
| [VTK-9.6.2/Rendering/Volume/LICENSE](VTK-9.6.2/Rendering/Volume/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/Rendering/Volume/LICENSE) |
| [VTK-9.6.2/ThirdParty/cgns/vtkcgns/license.txt](VTK-9.6.2/ThirdParty/cgns/vtkcgns/license.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/cgns/vtkcgns/license.txt) |
| [VTK-9.6.2/ThirdParty/cli11/vtkcli11/LICENSE](VTK-9.6.2/ThirdParty/cli11/vtkcli11/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/cli11/vtkcli11/LICENSE) |
| [VTK-9.6.2/ThirdParty/diy2/vtkdiy2/LICENSE.txt](VTK-9.6.2/ThirdParty/diy2/vtkdiy2/LICENSE.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/diy2/vtkdiy2/LICENSE.txt) |
| [VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.BSD](VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.BSD) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/eigen/vtkeigen/COPYING.BSD) |
| [VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.MINPACK](VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.MINPACK) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/eigen/vtkeigen/COPYING.MINPACK) |
| [VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.MPL2](VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.MPL2) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/eigen/vtkeigen/COPYING.MPL2) |
| [VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.README](VTK-9.6.2/ThirdParty/eigen/vtkeigen/COPYING.README) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/eigen/vtkeigen/COPYING.README) |
| [VTK-9.6.2/ThirdParty/exodusII/vtkexodusII/COPYRIGHT](VTK-9.6.2/ThirdParty/exodusII/vtkexodusII/COPYRIGHT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/exodusII/vtkexodusII/COPYRIGHT) |
| [VTK-9.6.2/ThirdParty/expat/vtkexpat/COPYING](VTK-9.6.2/ThirdParty/expat/vtkexpat/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/expat/vtkexpat/COPYING) |
| [VTK-9.6.2/ThirdParty/exprtk/vtkexprtk/license.txt](VTK-9.6.2/ThirdParty/exprtk/vtkexprtk/license.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/exprtk/vtkexprtk/license.txt) |
| [VTK-9.6.2/ThirdParty/fast_float/vtkfast_float/LICENSE-MIT](VTK-9.6.2/ThirdParty/fast_float/vtkfast_float/LICENSE-MIT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/fast_float/vtkfast_float/LICENSE-MIT) |
| [VTK-9.6.2/ThirdParty/fides/vtkfides/LICENSE.txt](VTK-9.6.2/ThirdParty/fides/vtkfides/LICENSE.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/fides/vtkfides/LICENSE.txt) |
| [VTK-9.6.2/ThirdParty/fides/vtkfides/thirdparty/rapidjson/fidesrapidjson/license.txt](VTK-9.6.2/ThirdParty/fides/vtkfides/thirdparty/rapidjson/fidesrapidjson/license.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/fides/vtkfides/thirdparty/rapidjson/fidesrapidjson/license.txt) |
| [VTK-9.6.2/ThirdParty/fmt/vtkfmt/LICENSE](VTK-9.6.2/ThirdParty/fmt/vtkfmt/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/fmt/vtkfmt/LICENSE) |
| [VTK-9.6.2/ThirdParty/freetype/vtkfreetype/LICENSE.TXT](VTK-9.6.2/ThirdParty/freetype/vtkfreetype/LICENSE.TXT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/freetype/vtkfreetype/LICENSE.TXT) |
| [VTK-9.6.2/ThirdParty/freetype/vtkfreetype/docs/FTL.TXT](VTK-9.6.2/ThirdParty/freetype/vtkfreetype/docs/FTL.TXT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/freetype/vtkfreetype/docs/FTL.TXT) |
| [VTK-9.6.2/ThirdParty/freetype/vtkfreetype/src/bdf/README](VTK-9.6.2/ThirdParty/freetype/vtkfreetype/src/bdf/README) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/freetype/vtkfreetype/src/bdf/README) |
| [VTK-9.6.2/ThirdParty/freetype/vtkfreetype/src/pcf/README](VTK-9.6.2/ThirdParty/freetype/vtkfreetype/src/pcf/README) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/freetype/vtkfreetype/src/pcf/README) |
| [VTK-9.6.2/ThirdParty/gl2ps/vtkgl2ps/COPYING.GL2PS](VTK-9.6.2/ThirdParty/gl2ps/vtkgl2ps/COPYING.GL2PS) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/gl2ps/vtkgl2ps/COPYING.GL2PS) |
| [VTK-9.6.2/ThirdParty/gl2ps/vtkgl2ps/COPYING.LGPL](VTK-9.6.2/ThirdParty/gl2ps/vtkgl2ps/COPYING.LGPL) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/gl2ps/vtkgl2ps/COPYING.LGPL) |
| [VTK-9.6.2/ThirdParty/glad/vtkglad/LICENSE](VTK-9.6.2/ThirdParty/glad/vtkglad/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/glad/vtkglad/LICENSE) |
| [VTK-9.6.2/ThirdParty/h5hut/vtkh5hut/COPYING](VTK-9.6.2/ThirdParty/h5hut/vtkh5hut/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/h5hut/vtkh5hut/COPYING) |
| [VTK-9.6.2/ThirdParty/h5hut/vtkh5hut/license.txt](VTK-9.6.2/ThirdParty/h5hut/vtkh5hut/license.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/h5hut/vtkh5hut/license.txt) |
| [VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/COPYING](VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/hdf5/vtkhdf5/COPYING) |
| [VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/COPYING_LBNL_HDF5](VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/COPYING_LBNL_HDF5) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/hdf5/vtkhdf5/COPYING_LBNL_HDF5) |
| [VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/src/H5FDsubfiling/mercury/LICENSE.txt](VTK-9.6.2/ThirdParty/hdf5/vtkhdf5/src/H5FDsubfiling/mercury/LICENSE.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/hdf5/vtkhdf5/src/H5FDsubfiling/mercury/LICENSE.txt) |
| [VTK-9.6.2/ThirdParty/ioss/vtkioss/COPYRIGHT](VTK-9.6.2/ThirdParty/ioss/vtkioss/COPYRIGHT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/ioss/vtkioss/COPYRIGHT) |
| [VTK-9.6.2/ThirdParty/jpeg/vtkjpeg/LICENSE.md](VTK-9.6.2/ThirdParty/jpeg/vtkjpeg/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/jpeg/vtkjpeg/LICENSE.md) |
| [VTK-9.6.2/ThirdParty/jsoncpp/vtkjsoncpp/LICENSE](VTK-9.6.2/ThirdParty/jsoncpp/vtkjsoncpp/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/jsoncpp/vtkjsoncpp/LICENSE) |
| [VTK-9.6.2/ThirdParty/kissfft/vtkkissfft/COPYING](VTK-9.6.2/ThirdParty/kissfft/vtkkissfft/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/kissfft/vtkkissfft/COPYING) |
| [VTK-9.6.2/ThirdParty/libharu/vtklibharu/LICENSE](VTK-9.6.2/ThirdParty/libharu/vtklibharu/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/libharu/vtklibharu/LICENSE) |
| [VTK-9.6.2/ThirdParty/libproj/vtklibproj/COPYING](VTK-9.6.2/ThirdParty/libproj/vtklibproj/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/libproj/vtklibproj/COPYING) |
| [VTK-9.6.2/ThirdParty/libxml2/vtklibxml2/Copyright](VTK-9.6.2/ThirdParty/libxml2/vtklibxml2/Copyright) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/libxml2/vtklibxml2/Copyright) |
| [VTK-9.6.2/ThirdParty/loguru/vtkloguru/LICENSE](VTK-9.6.2/ThirdParty/loguru/vtkloguru/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/loguru/vtkloguru/LICENSE) |
| [VTK-9.6.2/ThirdParty/lz4/vtklz4/lib/LICENSE](VTK-9.6.2/ThirdParty/lz4/vtklz4/lib/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/lz4/vtklz4/lib/LICENSE) |
| [VTK-9.6.2/ThirdParty/lzma/vtklzma/COPYING](VTK-9.6.2/ThirdParty/lzma/vtklzma/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/lzma/vtklzma/COPYING) |
| [VTK-9.6.2/ThirdParty/mpi4py/vtkmpi4py/LICENSE.rst](VTK-9.6.2/ThirdParty/mpi4py/vtkmpi4py/LICENSE.rst) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/mpi4py/vtkmpi4py/LICENSE.rst) |
| [VTK-9.6.2/ThirdParty/netcdf/vtknetcdf/COPYRIGHT](VTK-9.6.2/ThirdParty/netcdf/vtknetcdf/COPYRIGHT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/netcdf/vtknetcdf/COPYRIGHT) |
| [VTK-9.6.2/ThirdParty/nlohmannjson/vtknlohmannjson/LICENSE.MIT](VTK-9.6.2/ThirdParty/nlohmannjson/vtknlohmannjson/LICENSE.MIT) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/nlohmannjson/vtknlohmannjson/LICENSE.MIT) |
| [VTK-9.6.2/ThirdParty/ogg/vtkogg/COPYING](VTK-9.6.2/ThirdParty/ogg/vtkogg/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/ogg/vtkogg/COPYING) |
| [VTK-9.6.2/ThirdParty/pegtl/vtkpegtl/LICENSE](VTK-9.6.2/ThirdParty/pegtl/vtkpegtl/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/pegtl/vtkpegtl/LICENSE) |
| [VTK-9.6.2/ThirdParty/png/vtkpng/LICENSE](VTK-9.6.2/ThirdParty/png/vtkpng/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/png/vtkpng/LICENSE) |
| [VTK-9.6.2/ThirdParty/pugixml/vtkpugixml/LICENSE.md](VTK-9.6.2/ThirdParty/pugixml/vtkpugixml/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/pugixml/vtkpugixml/LICENSE.md) |
| [VTK-9.6.2/ThirdParty/scn/vtkscn/LICENSE](VTK-9.6.2/ThirdParty/scn/vtkscn/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/scn/vtkscn/LICENSE) |
| [VTK-9.6.2/ThirdParty/scn/vtkscn/LICENSE.nanorange](VTK-9.6.2/ThirdParty/scn/vtkscn/LICENSE.nanorange) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/scn/vtkscn/LICENSE.nanorange) |
| [VTK-9.6.2/ThirdParty/theora/vtktheora/COPYING](VTK-9.6.2/ThirdParty/theora/vtktheora/COPYING) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/theora/vtktheora/COPYING) |
| [VTK-9.6.2/ThirdParty/theora/vtktheora/LICENSE](VTK-9.6.2/ThirdParty/theora/vtktheora/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/theora/vtktheora/LICENSE) |
| [VTK-9.6.2/ThirdParty/tiff/vtktiff/LICENSE.md](VTK-9.6.2/ThirdParty/tiff/vtktiff/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/tiff/vtktiff/LICENSE.md) |
| [VTK-9.6.2/ThirdParty/token/vtktoken/license.md](VTK-9.6.2/ThirdParty/token/vtktoken/license.md) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/token/vtktoken/license.md) |
| [VTK-9.6.2/ThirdParty/utf8/vtkutf8/LICENSE](VTK-9.6.2/ThirdParty/utf8/vtkutf8/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/utf8/vtkutf8/LICENSE) |
| [VTK-9.6.2/ThirdParty/verdict/vtkverdict/LICENSE](VTK-9.6.2/ThirdParty/verdict/vtkverdict/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/verdict/vtkverdict/LICENSE) |
| [VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/LICENSE.txt](VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/LICENSE.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/viskores/vtkviskores/viskores/LICENSE.txt) |
| [VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/diy/viskoresdiy/LICENSE.txt](VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/diy/viskoresdiy/LICENSE.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/diy/viskoresdiy/LICENSE.txt) |
| [VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lcl/viskoreslcl/LICENSE.md](VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lcl/viskoreslcl/LICENSE.md) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lcl/viskoreslcl/LICENSE.md) |
| [VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lodepng/viskoreslodepng/LICENSE](VTK-9.6.2/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lodepng/viskoreslodepng/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/viskores/vtkviskores/viskores/viskores/thirdparty/lodepng/viskoreslodepng/LICENSE) |
| [VTK-9.6.2/ThirdParty/vpic/vtkvpic/LICENSE](VTK-9.6.2/ThirdParty/vpic/vtkvpic/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/vpic/vtkvpic/LICENSE) |
| [VTK-9.6.2/ThirdParty/xdmf2/vtkxdmf2/Copyright.txt](VTK-9.6.2/ThirdParty/xdmf2/vtkxdmf2/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/xdmf2/vtkxdmf2/Copyright.txt) |
| [VTK-9.6.2/ThirdParty/xdmf3/vtkxdmf3/Copyright.txt](VTK-9.6.2/ThirdParty/xdmf3/vtkxdmf3/Copyright.txt) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/xdmf3/vtkxdmf3/Copyright.txt) |
| [VTK-9.6.2/ThirdParty/zlib/vtkzlib/LICENSE](VTK-9.6.2/ThirdParty/zlib/vtkzlib/LICENSE) | [原始文本](https://raw.githubusercontent.com/Kitware/VTK/f49a1dbafa60b58ef22f6292ec58370453162192/ThirdParty/zlib/vtkzlib/LICENSE) |
