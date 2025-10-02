"""Collection of FastAPI routers used by the admin backend."""
from .import_students import create_students_import_router
from .import_supervisors import create_supervisors_import_router
from .matching import create_matching_router

__all__ = [
    "create_students_import_router",
    "create_supervisors_import_router",
    "create_matching_router",
]
