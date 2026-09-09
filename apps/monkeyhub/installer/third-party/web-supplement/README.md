# Supplemental web dependency notices

These files supplement the original licenses copied from the locked production web dependencies. Retrieved or extracted on 2026-09-09.

- `fflate-0.8.2-LICENSE.txt`: unmodified upstream MIT license for the fflate 0.8.2 copy vendored in `three/examples/jsm/libs/fflate.module.js`. The vendored file explicitly declares version 0.8.2. Official tag `v0.8.2` resolves to commit `d3243651cb142e3e04f3e4bc037b9e985878f444`. Source: https://raw.githubusercontent.com/101arrowz/fflate/d3243651cb142e3e04f3e4bc037b9e985878f444/LICENSE
- `rhino3dm-8.32.2-LICENSE.txt`: unmodified upstream MIT license from the official `8.32.2` tag, resolving to commit `617994a95bf0675f083aa2231ea27f9e66306855`. Source: https://raw.githubusercontent.com/mcneel/rhino3dm/617994a95bf0675f083aa2231ea27f9e66306855/LICENSE . The npm 8.32.2 metadata declares MIT and the `mcneel/rhino3dm` repository but supplies no `gitHead`; this is the matching official version tag, not an independently established npm tarball-to-commit identity. Package metadata: https://registry.npmjs.org/rhino3dm/8.32.2
- `three-0.185.1-EXRLoader-NOTICES.txt`: the complete TinyEXR and OpenEXR notice block, extracted byte-for-byte from lines 17-80 of `three/examples/jsm/loaders/EXRLoader.js` in the installed three 0.185.1 package used for the Studio production build. Original comment markers are retained; no implementation code is included. Package source: https://www.npmjs.com/package/three/v/0.185.1

The EXR notice source was the Studio build snapshot at `E:/MonkeyHubBuild/20260909/web-build-preflight/source/apps/archflow-studio/web/node_modules/three/examples/jsm/loaders/EXRLoader.js`.
