"""Printer envelopes for model preparation, before slicer-specific supports or brims."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PrinterProfile:
    key: str
    label: str
    nominal_volume_mm: tuple[float, float, float]
    usable_origin_mm: tuple[float, float, float]
    usable_volume_mm: tuple[float, float, float]
    notes: str
    sources: tuple[str, ...]

    def working_volume(
        self, xy_margin_mm: float = 5.0, z_clearance_mm: float = 5.0
    ) -> tuple[float, float, float]:
        """Apply tool-defined XY edge margins and one top clearance, in millimetres."""
        for name, value in (
            ("xy_margin_mm", xy_margin_mm),
            ("z_clearance_mm", z_clearance_mm),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        width, depth, height = self.usable_volume_mm
        volume = (
            width - 2 * xy_margin_mm,
            depth - 2 * xy_margin_mm,
            height - z_clearance_mm,
        )
        if any(value <= 0 for value in volume):
            raise ValueError("margins leave no positive working volume")
        return volume


_H2D_SOURCES = (
    "https://csm.bblcdn.com/hub/4668d0ca43994ff3bff4b37f1a65c2e7.pdf#page=70",
    "https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20H2D%200.4%20nozzle.json",
)

PROFILES = {
    "x1c": PrinterProfile(
        key="x1c",
        label="Bambu Lab X1 Carbon",
        nominal_volume_mm=(256.0, 256.0, 256.0),
        usable_origin_mm=(18.0, 0.0, 0.0),
        usable_volume_mm=(238.0, 256.0, 250.0),
        notes=(
            "标称体积为 256 × 256 × 256 mm。官方默认切片高度为 250 mm，"
            "左前角 X=0…18、Y=0…28 mm 为禁区。MonkeyFab 采用避开禁区的"
            "保守矩形 X=18…256、Y=0…256；238 mm 宽是工具推导值。"
            "额外 XY 边距与顶部留量是工具设定，不是厂家规格。"
        ),
        sources=(
            "https://public-cdn.bambulab.com/store/X1-Carbon%20tech%20specs.pdf",
            "https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20X1%20Carbon%200.4%20nozzle.json",
            "https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/fdm_bbl_3dp_001_common.json",
            "https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/fdm_machine_common.json",
        ),
    ),
    "h2s": PrinterProfile(
        key="h2s",
        label="Bambu Lab H2S",
        nominal_volume_mm=(340.0, 320.0, 340.0),
        usable_origin_mm=(0.0, 0.0, 0.0),
        usable_volume_mm=(340.0, 320.0, 340.0),
        notes=(
            "按 Bambu Studio v02.05.00.66 官方 H2S 0.4 mm 喷嘴配置："
            "X=0…340、Y=0…320 mm，最高 340 mm，无排除区。"
            "额外 XY 边距与顶部留量是工具设定，不是厂家规格。"
        ),
        sources=(
            "https://github.com/bambulab/BambuStudio/blob/v02.05.00.66/resources/profiles/BBL/machine/Bambu%20Lab%20H2S%200.4%20nozzle.json",
        ),
    ),
    "h2d-left": PrinterProfile(
        key="h2d-left",
        label="Bambu Lab H2D — left nozzle",
        nominal_volume_mm=(325.0, 320.0, 320.0),
        usable_origin_mm=(0.0, 0.0, 0.0),
        usable_volume_mm=(325.0, 320.0, 320.0),
        notes=(
            "按官方 2026.02 手册第 69–70 页：左喷头 X=0…325、Y=0…320 mm，"
            "最高 320 mm。此模式不使用两喷头的 350 mm 覆盖并集。"
            "额外 XY 边距与顶部留量是工具设定，不是厂家规格。"
        ),
        sources=_H2D_SOURCES,
    ),
    "h2d-right": PrinterProfile(
        key="h2d-right",
        label="Bambu Lab H2D — right nozzle",
        nominal_volume_mm=(325.0, 320.0, 325.0),
        usable_origin_mm=(25.0, 0.0, 0.0),
        usable_volume_mm=(325.0, 320.0, 325.0),
        notes=(
            "按官方 2026.02 手册第 69–70 页：右喷头 X=25…350、Y=0…320 mm，"
            "最高 325 mm。此模式不使用两喷头的 350 mm 覆盖并集。"
            "额外 XY 边距与顶部留量是工具设定，不是厂家规格。"
        ),
        sources=_H2D_SOURCES,
    ),
    "h2d-dual": PrinterProfile(
        key="h2d-dual",
        label="Bambu Lab H2D — shared dual-nozzle envelope",
        nominal_volume_mm=(300.0, 320.0, 320.0),
        usable_origin_mm=(25.0, 0.0, 0.0),
        usable_volume_mm=(300.0, 320.0, 320.0),
        notes=(
            "取官方 2026.02 手册左右喷头范围的交集：X=25…325、Y=0…320 mm，"
            "共同最高 320 mm。300 × 320 × 320 mm 是两喷头均可覆盖的保守几何范围，"
            "不等同于 350 mm 宽的平台并集。额外 XY 边距与顶部留量是工具设定，"
            "不是厂家规格。"
        ),
        sources=_H2D_SOURCES,
    ),
}


def get_profile(name: str) -> PrinterProfile:
    """Resolve a printer name; the short H2D alias uses the shared envelope."""
    key = "h2d-dual" if name == "h2d" else name
    try:
        return PROFILES[key]
    except KeyError as exc:
        choices = ", ".join((*PROFILES, "h2d"))
        raise ValueError(f"unknown printer profile {name!r}; choose {choices}") from exc
