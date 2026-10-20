"""Chat-side environment brief: concrete deliverables, not automatic geometry approval."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Metres = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False, strict=True)]


class EnvironmentBrief(BaseModel):
    """Validated intent shared by the chat planner, artist and interoperability roles."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=5, max_length=8000)
    style: str = Field(default="Grounded, physically plausible, original environment", max_length=1000)
    size_m: tuple[Metres, Metres, Metres] = (12.0, 9.0, 4.2)
    zones: list[str] = Field(default_factory=list, max_length=20)
    detail: Literal["high", "hero"] = "high"
    target: Literal["blender", "blender+unreal"] = "blender+unreal"
    seed: int = Field(default=42, ge=0, le=2**31 - 1, strict=True)
    triangle_budget: int = Field(default=2_000_000, ge=10_000, le=20_000_000, strict=True)
    texture_resolution: Literal[512, 1024, 2048, 4096] = 2048
    reference_policy: Literal["original-only", "licensed-with-provenance"] = "original-only"

    @field_validator("goal", "style", mode="before")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        if not value.strip():
            raise ValueError("Describe the intended environment; whitespace is not a brief.")
        return value.strip()

    @field_validator("zones")
    @classmethod
    def distinct_zones(cls, values: list[str]) -> list[str]:
        values = [value.strip() for value in values]
        if any(not value or len(value) > 160 for value in values):
            raise ValueError("Each zone must have a name of 1–160 characters.")
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("Zone names must be distinct.")
        return values


def plan_environment(brief: EnvironmentBrief | dict) -> dict:
    """Produce an actionable art/transfer plan without claiming the scene exists."""
    if not isinstance(brief, EnvironmentBrief):
        brief = EnvironmentBrief.model_validate(brief)
    unreal = brief.target == "blender+unreal"
    return {
        "schema": "studio-environment-plan/v1",
        "status": "planned",
        "brief": brief.model_dump(mode="json"),
        "assumptions": (
            ["Zone layout is to be proposed by the chat artist from the goal before modeling."]
            if not brief.zones else []
        ),
        "detail_requirements": {
            "architecture": "Complete occupied space: floor, wall and ceiling systems, openings, circulation, joints, edge treatment and infrastructure appropriate to the brief.",
            "functional_zones": "Equip every named zone with coherent working objects, furniture and interfaces; avoid empty rooms or a single isolated hero asset.",
            "secondary_geometry": "Visible joints, bevels, mounting systems, vents, cable routes and functional assemblies; curves and custom meshes where needed.",
            "surface_detail": "Scale-correct PBR surfaces, UVs and transferable texture maps; controlled material variation rather than uniform primitive colors.",
            "story_and_composition": "Original signage, purposeful props and an intentional arrangement visible from multiple interior camera positions.",
            "lighting": "Motivated practical lights, balanced exposure and deliberate focal hierarchy; inspect both wide and close views.",
            "hero_focus": (
                "Add close-camera construction and surface details to focal assets; inspect silhouettes, contacts and roughness at final viewing size."
                if brief.detail == "hero" else
                "Focal assets must withstand a close camera; primitives may be construction inputs, never the finished visual standard."
            ),
        },
        "construction": {
            "collection": "StudioEnvironment",
            "object_tags": ["studio_asset", "studio_category"],
            "presentation_collection": "PresentationRig",
            "units": "metres in Blender; centimetres in Unreal",
            "parameters": ["seed", "layout dimensions", "lighting preset", "camera selection"],
            "efficiency": "Instance reusable mesh data and textures. Use the triangle budget as a ceiling, not a detail score. Inspect RAM and disk before increasing it.",
            "provenance": "Record original creation or the license/source of every external asset; retain attribution. Never copy confidential client assets.",
        },
        "stages": [
            {"role": "planner", "deliver": "A spatial layout and asset/category list tied to the brief; record assumptions and budgets."},
            {"role": "native_modeler", "deliver": "A detailed editable Blender scene with named collections, reusable assets, materials, lights and cameras."},
            {"role": "geometry_qa", "deliver": "Fresh-process reopen, measured geometry, units, texture availability and saved native artifact hashes."},
            {"role": "reviewer", "deliver": "Review at least three real renders: wide interior, another occupied zone, and a close view. Correct intersections, empty areas, flat materials and unfinished shapes before export."},
            *([{"role": "interop", "deliver": "Export an environment manifest and local mesh assets. Reconstruct instances, transforms, materials, lights and cameras in a separate owned Unreal project. Reopen the saved level and compare native measurements."}] if unreal else []),
            *([{"role": "reviewer", "deliver": "Inspect a real Unreal render or viewport. Report visual transfer untested if the GPU or shader toolchain is unavailable; never substitute a Blender image as Unreal evidence."}] if unreal else []),
            {"role": "native_modeler", "deliver": "Demonstrate one meaningful chat-requested revision in a new native file, preserving the baseline; record what changed and revalidate."},
        ],
        "acceptance": {
            "native_integrity": ["nonempty editable scene", "fresh reopen", "finite transforms and positive dimensions", "no missing declared textures"],
            "coverage": ["all brief zones represented", "architecture and secondary detail present", "three useful camera views"],
            "visual_review": ["coherent layout and plausible scale", "finished focal assets", "readable lighting and materials", "no obvious floating props or intersections"],
            "transfer": (
                ["source and target instance counts", "world placement and scale", "materials/textures", "lights/cameras", "saved level reopened", "real Unreal visual review"]
                if unreal else []
            ),
            "rule": "Counts, hashes and process success establish integrity only. Visual quality and fulfillment of the brief require inspection. Record each result as observed, failed or untested.",
        },
        "execution": "The chat client interprets the brief, writes/revises host scripts and dispatches MCP actions. This plan neither executes native tools nor certifies a completed environment.",
    }
