"""The five specialists the orchestrator dispatches to."""

from .accommodation import accommodation_specialist
from .base import FunctionSpecialist, Specialist
from .destination_guide import destination_guide_specialist
from .dining import dining_specialist
from .itinerary import itinerary_specialist
from .transport import transport_specialist

ALL_SPECIALISTS = [
    itinerary_specialist,
    transport_specialist,
    accommodation_specialist,
    destination_guide_specialist,
    dining_specialist,
]

__all__ = [
    "ALL_SPECIALISTS",
    "FunctionSpecialist",
    "Specialist",
    "accommodation_specialist",
    "destination_guide_specialist",
    "dining_specialist",
    "itinerary_specialist",
    "transport_specialist",
]
