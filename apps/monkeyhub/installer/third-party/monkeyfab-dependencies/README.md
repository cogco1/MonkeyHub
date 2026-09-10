# MonkeyFab 依赖许可补充

本目录补充 MonkeyHub 随包 `paho-mqtt==2.1.0` 和 `manifold3d==3.5.3`
官方 wheel 未附带的上游许可原文。下载文件保留上游原始字节；安装包仍须完整保留
各 wheel 的 `.dist-info`、包内许可及附带 DLL，本目录不替代这些内容。

本次核对的发布文件是
[paho_mqtt-2.1.0-py3-none-any.whl](https://files.pythonhosted.org/packages/c4/cb/00451c3cf31790287768bb12c6bec834f5d292eaf3022afc88e14b8afc94/paho_mqtt-2.1.0-py3-none-any.whl)
和
[manifold3d-3.5.3-cp313-cp313-win_amd64.whl](https://files.pythonhosted.org/packages/09/bb/03142c8640985438e864e96b40d0bcc885973ac66461f03efea70b7a6b50/manifold3d-3.5.3-cp313-cp313-win_amd64.whl)。
Paho 的 `licenses/LICENSE.txt` 仅说明双许可并引用 `edl-v10` 和 `epl-v20`，
未附两份全文；此处保留这两份上游文本。Manifold 的 wheel 仅附自身的 Apache
`LICENSE` 和 `AUTHORS`，未附下面列出的静态依赖许可。

## Manifold 静态依赖及版本范围

Manifold v3.5.3 对应源码提交
`0edd9d54876f3135e431575214dd6d8a72866fee`：

- [wheel 构建配置](https://github.com/elalish/manifold/blob/0edd9d54876f3135e431575214dd6d8a72866fee/pyproject.toml)
  开启 `MANIFOLD_PAR` 和 `MANIFOLD_USE_BUILTIN_TBB`，指定 oneTBB v2022.3.0。
  [依赖配置](https://github.com/elalish/manifold/blob/0edd9d54876f3135e431575214dd6d8a72866fee/cmake/manifoldDeps.cmake)
  将内建依赖静态链接，并将 Clipper2 固定至
  `46f639177fe418f9689e8ddb74f08a870c71f5b4`。
- [Python 绑定配置](https://github.com/elalish/manifold/blob/0edd9d54876f3135e431575214dd6d8a72866fee/bindings/python/CMakeLists.txt)
  使用 `NB_STATIC`。构建依赖对 nanobind 指定 `>=1.8.0,<3.0.0`，
  CMake 在需要自行取得 nanobind 时使用 v2.12.0；这没有锁定发布 wheel 实际使用的
  nanobind 版本。本目录的 `nanobind-v2.12.0-reference-LICENSE` 是该后备版本的
  固定上游许可快照，不是 wheel 构建版本的认定。
- nanobind v2.12.0 的
  [内部实现](https://github.com/wjakob/nanobind/blob/2a61ad2494d09fecb2e13322c1383342c299900d/src/nb_internals.h)
  使用 `tsl::robin_map`，其
  [robin-map 子模块](https://github.com/wjakob/nanobind/tree/2a61ad2494d09fecb2e13322c1383342c299900d/ext)
  固定至 `4ec1bf19c6a96125ea22062f38c2cf5b958e448e`。
  此处相应 MIT 文本同样是参考快照；实际 wheel 内嵌 robin-map 的精确提交尚未确定。
- oneTBB 的 `third-party-programs.txt` 按上游原文保留；列入原文不表示其中每个项目
  都已被核实链接进该 wheel。Clipper2 的 Boost 许可全文一并保留；该许可对纯机器
  目标码豁免随附全文要求，不将单独缺少该文本视为本二进制包的发布阻塞。

## 原文及固定来源

| 本地原文 | 上游来源 |
| --- | --- |
| [paho-mqtt-2.1.0-edl-v10](paho-mqtt-2.1.0-edl-v10) | [Paho v2.1.0，提交 af64a436](https://raw.githubusercontent.com/eclipse-paho/paho.mqtt.python/af64a4365c6ac5a7a4d339e7b00f44df91353b35/edl-v10) |
| [paho-mqtt-2.1.0-epl-v20](paho-mqtt-2.1.0-epl-v20) | [Paho v2.1.0，提交 af64a436](https://raw.githubusercontent.com/eclipse-paho/paho.mqtt.python/af64a4365c6ac5a7a4d339e7b00f44df91353b35/epl-v20) |
| [nanobind-v2.12.0-reference-LICENSE](nanobind-v2.12.0-reference-LICENSE) | [nanobind v2.12.0，提交 2a61ad24](https://raw.githubusercontent.com/wjakob/nanobind/2a61ad2494d09fecb2e13322c1383342c299900d/LICENSE) |
| [robin-map-4ec1bf19-reference-LICENSE](robin-map-4ec1bf19-reference-LICENSE) | [robin-map，提交 4ec1bf19](https://raw.githubusercontent.com/Tessil/robin-map/4ec1bf19c6a96125ea22062f38c2cf5b958e448e/LICENSE) |
| [oneTBB-2022.3.0-LICENSE.txt](oneTBB-2022.3.0-LICENSE.txt) | [oneTBB v2022.3.0，提交 f1862f38](https://raw.githubusercontent.com/uxlfoundation/oneTBB/f1862f38f83568d96e814e469ab61f88336cc595/LICENSE.txt) |
| [oneTBB-2022.3.0-third-party-programs.txt](oneTBB-2022.3.0-third-party-programs.txt) | [oneTBB v2022.3.0，提交 f1862f38](https://raw.githubusercontent.com/uxlfoundation/oneTBB/f1862f38f83568d96e814e469ab61f88336cc595/third-party-programs.txt) |
| [Clipper2-46f63917-LICENSE](Clipper2-46f63917-LICENSE) | [Clipper2，提交 46f63917](https://raw.githubusercontent.com/AngusJohnson/Clipper2/46f639177fe418f9689e8ddb74f08a870c71f5b4/LICENSE) |

## Pillow 随包署名

`Pillow==12.3.0` 的完整许可继续保留在随包 Python runtime 的
`pillow-12.3.0.dist-info/licenses/LICENSE`。
[官方 cp313 Windows x64 wheel](https://files.pythonhosted.org/packages/a6/9b/7a58e61d62be561da3a356fe2384d4059a6345fc130e23ef1c36a5b81d24/pillow-12.3.0-cp313-cp313-win_amd64.whl)
的该文件包含 FreeType 2.14.3、libjpeg-turbo 3.1.4.1 及其他原生依赖的许可全文。
本包通过 Pillow 使用 FreeType 和 Independent JPEG Group 的成果：

This software is based in part on the work of the FreeType Team.

This software is based in part on the work of the Independent JPEG Group.

这些署名对应上述 wheel 中的 FreeType 和 IJG 条款。
