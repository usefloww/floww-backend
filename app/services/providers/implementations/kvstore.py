from pydantic import BaseModel

from app.services.providers.provider_setup import ProviderSetupStep
from app.services.providers.provider_utils import ProviderI


#### Provider ####
class KVStoreProviderState(BaseModel):
    """KVStore provider has no configuration - it's just a namespace identifier."""

    pass


class KVStoreProvider(ProviderI):
    name: str = "kvstore"
    setup_steps: list[ProviderSetupStep] = []
    model = KVStoreProviderState


# KVStore has no triggers (for now)
KVSTORE_TRIGGER_TYPES = {}
