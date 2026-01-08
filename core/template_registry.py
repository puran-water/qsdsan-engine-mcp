"""
Template Registry - Centralized flowsheet template definitions.

This module provides a single source of truth for available flowsheet templates,
ensuring consistency between the CLI and MCP adapters.
"""

from typing import Dict, List, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


class TemplateStatus(str, Enum):
    """Template implementation status."""
    AVAILABLE = "available"      # Fully implemented and tested
    PENDING = "pending"          # Implementation in progress
    PLANNED = "planned"          # Scheduled for future phase


@dataclass
class FlowsheetTemplate:
    """Definition of a flowsheet template."""
    name: str
    description: str
    model_type: str
    reactor_type: str
    category: str  # "anaerobic" or "aerobic"
    status: TemplateStatus = TemplateStatus.PLANNED

    # Optional metadata
    typical_hrt: str = ""  # e.g., "15-30 days" or "5-24 hours"
    reference: str = ""    # Implementation reference

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        result = asdict(self)
        result["status"] = self.status.value
        return result


# =============================================================================
# Template Definitions (Single Source of Truth)
# =============================================================================
TEMPLATES: Dict[str, FlowsheetTemplate] = {
    # Anaerobic templates
    "anaerobic_cstr_madm1": FlowsheetTemplate(
        name="anaerobic_cstr_madm1",
        description="Single CSTR with mADM1 model (63 components, 4 biogas species)",
        model_type="mADM1",
        reactor_type="AnaerobicCSTRmADM1",
        category="anaerobic",
        status=TemplateStatus.AVAILABLE,  # Phase 1B complete
        typical_hrt="15-30 days",
        reference="anaerobic-design-mcp",
    ),

    # Aerobic MBR templates
    "mle_mbr_asm2d": FlowsheetTemplate(
        name="mle_mbr_asm2d",
        description="MLE-MBR (anoxic -> aerobic -> MBR) with ASM2d",
        model_type="ASM2d",
        reactor_type="CSTR + CompletelyMixedMBR",
        category="aerobic",
        status=TemplateStatus.AVAILABLE,  # Phase 1C complete
        typical_hrt="5-24 hours",
        reference="Pune_Nanded_WWTP",
    ),
    "a2o_mbr_asm2d": FlowsheetTemplate(
        name="a2o_mbr_asm2d",
        description="A2O-MBR (anaerobic -> anoxic -> aerobic -> MBR) with EBPR",
        model_type="ASM2d",
        reactor_type="CSTR + CompletelyMixedMBR",
        category="aerobic",
        status=TemplateStatus.AVAILABLE,  # Phase 1C complete
        typical_hrt="6-24 hours",
        reference="Pune_Nanded_WWTP",
    ),
    "ao_mbr_asm2d": FlowsheetTemplate(
        name="ao_mbr_asm2d",
        description="Simple A/O-MBR configuration",
        model_type="ASM2d",
        reactor_type="CSTR + CompletelyMixedMBR",
        category="aerobic",
        status=TemplateStatus.AVAILABLE,  # Phase 1C complete
        typical_hrt="5-12 hours",
        reference="Pune_Nanded_WWTP",
    ),
}


# =============================================================================
# Query Functions
# =============================================================================
def get_template(name: str) -> FlowsheetTemplate:
    """
    Get a template by name.

    Args:
        name: Template name

    Returns:
        FlowsheetTemplate instance

    Raises:
        KeyError if template not found
    """
    if name not in TEMPLATES:
        raise KeyError(f"Unknown template: {name}. Available: {list(TEMPLATES.keys())}")
    return TEMPLATES[name]


def list_templates() -> Dict[str, List[Dict[str, Any]]]:
    """
    List all templates grouped by category.

    Returns:
        Dict with "anaerobic" and "aerobic" lists
    """
    anaerobic = []
    aerobic = []

    for template in TEMPLATES.values():
        t_dict = template.to_dict()
        if template.category == "anaerobic":
            anaerobic.append(t_dict)
        else:
            aerobic.append(t_dict)

    return {
        "anaerobic": anaerobic,
        "aerobic": aerobic,
    }


def list_available_templates() -> List[str]:
    """
    List names of templates that are fully available.

    Returns:
        List of template names with status=available
    """
    return [
        name for name, template in TEMPLATES.items()
        if template.status == TemplateStatus.AVAILABLE
    ]


def is_template_available(name: str) -> bool:
    """
    Check if a template is available for use.

    Args:
        name: Template name

    Returns:
        True if template exists and has status=available
    """
    template = TEMPLATES.get(name)
    return template is not None and template.status == TemplateStatus.AVAILABLE


def get_template_status(name: str) -> str:
    """
    Get the implementation status of a template.

    Args:
        name: Template name

    Returns:
        Status string ("available", "pending", "planned")
    """
    if name not in TEMPLATES:
        return "unknown"
    return TEMPLATES[name].status.value


def set_template_status(name: str, status: TemplateStatus) -> None:
    """
    Update a template's status.

    Used when a template implementation is completed.

    Args:
        name: Template name
        status: New status
    """
    if name in TEMPLATES:
        TEMPLATES[name].status = status
