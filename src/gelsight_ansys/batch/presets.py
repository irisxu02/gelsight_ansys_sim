"""Curated presets shared by validation, queue discovery, and export."""

INDENTER_CASES = {
    "press": "sphere_press",
    "slide": "sphere_slide",
    "twist": "sphere_twist",
    "soft": "soft_sphere_press",
    "rough": "rough_sphere_slide",
    "imported_rigid": "imported_rigid_press",
    "imported_soft": "imported_soft_press",
}
PLANE_CASES = {
    "plane_rigid_reference_press_slide": "rigid_reference",
    "plane_soft_rubber_press_slide": "soft_rubber",
    "plane_compressible_foam_press_slide": "compressible_foam",
    "plane_slippery_surface_press_slide": "slippery_surface",
    "plane_rough_surface_press_slide": "rough_surface",
    "plane_sticky_surface_press_slide": "sticky_surface",
    "plane_fluffy_fabric_press_slide": "fluffy_fabric",
}
CASES = {**INDENTER_CASES, **{name: name for name in PLANE_CASES}}


def preset_file(key):
    """The preset's path under configs/ for a catalog key."""
    if key in INDENTER_CASES:
        return f"{INDENTER_CASES[key]}.json"
    return f"material_plane_slide/{PLANE_CASES[key]}.json"
