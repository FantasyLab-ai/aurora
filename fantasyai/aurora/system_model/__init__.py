"""
System Model layer — synthetic understanding of what kind of system the
dataset describes.

Public API:

    SystemModel, system_model_to_json, system_model_from_dict
    list_templates, get_template, best_template_for, score_template
    instantiate_system_model, identify_domain
"""
from __future__ import annotations

from .schema import (
    SystemModel, Entity, Relationship, Topology, StateBlock,
    Constraint, CharacteristicPattern, Vocabulary, TemplateAction,
    ProvenanceBlock,
    system_model_to_json, system_model_from_dict,
)
from .templates_loader import (
    DomainTemplate, IdentificationRules, IdentificationScore,
    list_templates, get_template, score_template, best_template_for,
)
from .instantiator import instantiate_system_model, identify_domain
from .intervention import propagate_intervention, EntityDelta, PropagationResult
from .simulator import simulate_forward, SimResult, SimStep

__all__ = [
    # schema
    "SystemModel", "Entity", "Relationship", "Topology", "StateBlock",
    "Constraint", "CharacteristicPattern", "Vocabulary", "TemplateAction",
    "ProvenanceBlock",
    "system_model_to_json", "system_model_from_dict",
    # templates
    "DomainTemplate", "IdentificationRules", "IdentificationScore",
    "list_templates", "get_template", "score_template", "best_template_for",
    # instantiator
    "instantiate_system_model", "identify_domain",
    # intervention + simulator
    "propagate_intervention", "EntityDelta", "PropagationResult",
    "simulate_forward", "SimResult", "SimStep",
]
