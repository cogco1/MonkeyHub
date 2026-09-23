# SKP / DWG execution providers — investigation for #259

Checked 2026-09-23. Documentation confirms API features, not a tested MonkeyHub
integration. **已验证** below means our actual conversion tests, unless explicitly
labelled “official documentation only”. Installation does not prove entitlement,
compatible plugins, an interactive session, or a validated executor.

| Path | Evidence and limits | Verification / prerequisites |
|---|---|---|
| Existing in-process 3DM ↔ GLB | Bounded meshes; native 3DM reopen and independent Three.js GLB readback, with meter/axis/count checks | **已验证** for documented fixtures; no general CAD round-trip claim |
| SketchUp desktop + Ruby bridge | `Model#import`, `save`/`save_copy`, and `export` are documented. DWG/DXF exporters depend on product/version; GLB export starts at 2024. A matching importer must be checked separately: an exporter does not imply reverse import. Treat this as GUI-hosted local execution, not an unattended/headless server contract. | **需要本机软件** and a functioning signed/trusted extension, correct user session and product entitlement; MonkeyHub bridge/cold reopen **尚未验证** |
| SketchUp C API / Desktop SDK | Official SDK reads/writes SKP. Windows and macOS distributions have distinct binaries/ABI/toolchain requirements. This is separate from automating the desktop Ruby model. #53 owns the prior limited local spike. | **尚未验证** for product deployment; request SDK access and establish applicable redistribution/deployment rights (**需要商业授权确认**, not an assertion that every SDK use is paid). No bundled DLL may be treated as redistribution permission. |
| AutoCAD Core Console | Autodesk's tutorial demonstrates scripts/batch processing, drawing saves and DXF workflows. Investigate open/edit/save DWG, DWG version conversion, DXF and plot-to-PDF under the exact installed engine/commands. GUI-dependent commands and add-ins cannot be assumed to work. No evidence here establishes a built-in SKP/GLB/3DM interchange route. | **需要本机软件**: entitled Windows AutoCAD installation containing `accoreconsole.exe`; usable license, engine/add-in version and PDF device configuration. Every proposed MonkeyHub native route and independent reopen **尚未验证** |
| AutoCAD desktop | Installation can be detected independently of Core Console; `acad.exe` is not a headless substitute. | **需要本机软件** and desktop automation integration; **尚未验证**. Mac AutoCAD installation must not imply Windows Core Console availability. |
| SketchUp SDK / ODA / RealDWG / APS future providers | Separate server-side implementations of the same provider contract. ODA membership/deployment terms, RealDWG commercial agreement, and APS account/service terms must be established for the intended use. APS offers cloud AutoCAD automation. | **需要商业授权/服务授权** as applicable and actual runtime configuration; all **尚未验证** in MonkeyHub. Nothing is downloaded, installed or contacted by capability probing. |

Sources (primary/vendor documentation):

- [SketchUp Model Ruby API](https://ruby.sketchup.com/Sketchup/Model.html)
- [SketchUp exporter options](https://ruby.sketchup.com/file.exporter_options.html)
- [Desktop SDK access](https://developer.sketchup.com/)
- [C API overview / platform requirements](https://extensions.sketchup.com/developers/sketchup_c_api/sketchup/index.html)
- [SketchUp OS, graphics and subscription authorization requirements](https://help.sketchup.com/en/sketchup/system-requirements)
- [Autodesk University Core Console scripting tutorial](https://static.au-uw2-prd.autodesk.com/Class_Handout_BES227196_AutoCAD_Scripting_from_the_Core_AutoCAD_Core_Console_Mike_Best.pdf)
- [Autodesk offering/license terms](https://www.autodesk.com/company/terms-of-use/en/offering-types-and-benefits)
- [APS automation](https://aps.autodesk.com/automation-apis)
- [ODA membership/deployment](https://www.opendesign.com/oda-membership)
- [Autodesk commercial OEM/RealDWG agreements](https://www.autodesk.com/partner-program/customized-applications)
- [#53 prior local spike and unresolved acceptance](https://github.com/cogco1/MonkeyHub/issues/53)

Desktop OS/version support changes: consult the requirements for the actual
installed release. No Linux SketchUp desktop or macOS/Linux Core Console support
is asserted. The current probe has no qualified native version allow-list;
directory version hints are not executable version verification. License type
cannot be determined from installed files; this investigation grants no license.

## Implementation plan and boundaries

Extend `adapters.cad_execution` with a provider protocol, read-only native
discovery and coordinator. Retain the existing mesh adapters. Extend
`studio.artifacts` reports/capabilities through existing jobs and P036, without
another queue, settings store or UI picker. Only verified, available providers
are eligible; local application executors rank before in-process/SDK/cloud
alternatives. No retry through another provider after conversion starts.

Local software providers in this PR only probe and explain unavailable execution.
They do not launch/attach to a desktop, load a vendor DLL, write Ruby/SCR scripts,
check out a license or use cloud credentials. The future extension point is an
explicit server-supplied provider list, not automatic plugin loading or a new
registry of executable commands.

Native deliveries and previews are separate artifacts. A 2D DWG preview should
remain PDF/SVG/raster; it is not a GLB requirement. Unknown dimensionality is not
permission to create 3D geometry. No new preview generator is implemented here.

## Kevin's next local experiments

1. SketchUp: record exact desktop/build/OS and entitlement; install a bounded
   bridge, open a non-private SKP, save a separate SKP, export supported GLB and
   PNG, and reopen the native file independently. Cover groups/instances/tags,
   textures, units and transforms. Verify unsupported input import separately.
   Do not modify the user's currently open unsaved model. Test busy/modal dialogs,
   session loss, cancellation and version incompatibility. Continue #53 SDK terms.
2. AutoCAD: record Core Console build and compatible plugins; run bounded
   open/edit/save/version/DXF/plot-PDF cases, including a 2D-only DWG. Independently
   reopen native output and compare units, entities, layers, bounds and layout.
   Record font/XREF/proxy-entity losses. An installed executable alone is not PASS.
3. Only then add a concrete native provider with validated route declarations,
   license/runtime checks, output validation and per-job provenance. Server/cloud
   alternatives remain a separate explicitly configured implementation.
