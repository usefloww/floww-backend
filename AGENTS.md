# Developer Preferences & Coding Guidelines

This document outlines the architectural and coding preferences for this project to ensure consistency and maintainability.


## Development

### Testing

Run tests by using `just test-unit <space seperated file paths>`

### Migrations

Don't write migrations yourself, use `just generate-migrations` and afterwards rename the file to contain an appropriate number and name.

To run the migrations after you created them you can run `just migrate`

## Architecture Principles

### Composition Over Inheritance

**Prefer composition over inheritance.** If you have common helpers that are used across multiple classes (e.g., OIDC and WorkOS providers), extract them into utility functions rather than methods on a base class.

**Bad:**
```python
class AuthProvider(ABC):
    def _fetch_jwks(self, url: str) -> dict:
        # Common JWKS fetching logic
        pass

class OIDCProvider(AuthProvider):
    def get_public_keys(self):
        return self._fetch_jwks(self.jwks_url)
```

**Good:**
```python
# In utils/jwt_utils.py
def fetch_jwks(url: str) -> dict:
    # Pure function, easily testable
    pass

class OIDCProvider(AuthProvider):
    async def get_jwks_url(self) -> str:
        return self.jwks_url
```

**Rule of thumb:** Only keep methods on classes if they need access to instance state. If a method can be a pure function, make it one.

### Avoid Type-Based Conditionals

**Never use `isinstance()` checks or type-based conditionals in utility code.** This is a code smell indicating poor abstraction.

**Bad:**
```python
if isinstance(provider, OIDCProvider):
    issuer = await provider.get_issuer_from_discovery()
else:
    issuer = provider.get_issuer()
```

**Good:**
```python
# Make the interface uniform
issuer = await provider.get_issuer()  # All providers implement async version
```

If you find yourself writing type checks, it means your abstract interface is incomplete or inconsistent.

### Separation of Concerns

**Module instantiation should not live inside the module itself.** For example, provider instantiation should not be in `app/auth/provider_factory.py` - that's not the auth module's responsibility.

**Good structure:**
- `app/auth/` - Contains provider interfaces and implementations
- `app/deps/provider.py` - Contains provider instantiation logic
- `app/utils/` - Contains pure utility functions

### Caching Pattern

**Use `@lru_cache` for singleton-like behavior** instead of manual global variables.

**Bad:**
```python
_provider_instance = None

def get_provider():
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = create_provider()
    return _provider_instance
```

**Good:**
```python
from functools import lru_cache

@lru_cache
def get_provider():
    return create_provider()
```

## Code Style

### Documentation

**Only document non-obvious code.** Do not write docstrings for:
- One-line functions
- Simple getters/setters
- Functions where the name and signature are self-explanatory

**Bad:**
```python
def get_issuer(self) -> str:
    """
    Get the expected JWT issuer value.

    Returns:
        Expected 'iss' claim value for JWT validation
    """
    return self.issuer_url
```

**Good:**
```python
def get_issuer(self) -> str:
    return self.issuer_url
```

**Do document:**
- Complex algorithms
- Non-obvious business logic
- Functions with subtle edge cases or important behavior
- Public APIs that need usage examples

### Configuration

**Prefer direct imports from settings.** Classes should import and use `settings` directly rather than having configuration injected.

**Preferred:**
```python
from app.settings import settings

class OIDCProvider:
    def __init__(self):
        self.client_id = settings.AUTH_CLIENT_ID
```

**Not preferred:**
```python
class OIDCProvider:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id  # Too much ceremony for dependency injection
```

Rationale: Simpler code, easier to understand. If you need testability, mock `settings` at the module level.

### Class Design

**Keep only relevant methods public on the class interface.**

- If a method is only used internally, make it private (`_method_name`) or extract it to a utility function
- If a method doesn't need instance state, make it a utility function
- Abstract base classes should define the minimal interface, not convenience methods

**Example:**
```python
# Bad - too many public methods
class OIDCProvider:
    def get_jwks_url(self) -> str: ...
    def get_jwks_url_from_discovery(self) -> str: ...  # Redundant!
    def get_issuer(self) -> str: ...
    def get_issuer_from_discovery(self) -> str: ...  # Redundant!

# Good - single clear interface
class OIDCProvider:
    async def get_jwks_url(self) -> str: ...
    async def get_issuer(self) -> str: ...
```

## Testing Philosophy

- Pure functions are easier to test than class methods
- Prefer stateless utility functions that take all inputs as parameters
- Cache at module level, not instance level (easier to clear in tests)
- If you need to mock settings, do it at import time

## Common Patterns

### Module-Level Caching

**Preferred caching pattern:**
```python
# At module level
_cache: dict[str, Any] = {}

async def fetch_something(key: str) -> Any:
    if key in _cache:
        return _cache[key]

    result = await expensive_operation(key)
    _cache[key] = result
    return result
```

This is better than instance-level caching because:
1. Cache is shared across all instances
2. Easy to clear in tests
3. No need to track instance lifecycle

### Utility Module Organization

Organize utils by concern, not by layer:
- `utils/jwt_utils.py` - JWT operations (fetch, decode, validate)
- `utils/session.py` - Session/cookie management
- `utils/auth.py` - High-level auth coordination

Not:
- `utils/helpers.py` - Generic dumping ground
- `utils/common.py` - Unclear purpose

## When to Refactor

Consider refactoring when you see:
1. **`isinstance()` checks** - Your interface is incomplete
2. **Methods that don't use `self`** - Should be utility functions
3. **Verbose docstrings on obvious code** - Remove them
4. **Duplicate method names** (e.g., `get_issuer` and `get_issuer_from_discovery`) - Simplify interface
5. **Factory living in the module it creates** - Move to `deps/`

## Summary

- Composition > Inheritance
- Pure functions > Class methods (when possible)
- No type checking in business logic
- Minimal documentation on obvious code
- Flat, clear interfaces
- Concerns properly separated across modules
