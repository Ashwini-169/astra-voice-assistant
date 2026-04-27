# Voice2 - Development Guidelines

## Code Quality Standards

### Module Structure
- Every Python module starts with a module-level docstring describing its purpose
- `__init__.py` files are intentionally empty — packages are namespaces only, no re-exports
- Module-level constants use `UPPER_SNAKE_CASE` and are defined before classes/functions
- Private helpers are prefixed with `_` (functions and module-level variables)

### Imports
- Standard library first, then third-party, then local (`core.*`, `services.*`, etc.)
- Lazy imports inside functions for optional/heavy dependencies (e.g. `import edge_tts` inside async function)
- Graceful degradation for optional packages:
  ```python
  try:
      import miniaudio
  except ImportError:
      miniaudio = None  # type: ignore[assignment]
  ```
- `noqa` comments used sparingly and only with explanation: `# noqa: F401  (imported for type completeness)`

### Type Annotations
- All function signatures are fully annotated (parameters + return types)
- `Optional[T]` used for nullable values (not `T | None` union syntax in older files, mixed usage)
- `Dict`, `List`, `Tuple` from `typing` used in older files; newer files use built-in generics
- `-> None` always explicit on `__init__` and void methods

### Naming Conventions
- Classes: `PascalCase` (e.g. `EmotionStreamBuffer`, `AudioPlaybackEngine`, `AssistantStateController`)
- Functions/methods: `snake_case`
- Private methods/functions: `_snake_case`
- Module-level singletons: `_snake_case` (e.g. `_runtime_settings`, `_engine`, `_network_state`)
- Constants: `UPPER_SNAKE_CASE` (e.g. `KNOWN_EMOTIONS`, `DEFAULT_CHUNK_INITIAL_WORDS`)
- Enum values: `UPPER_CASE` string values in lowercase (e.g. `IDLE = "idle"`)

### Docstrings
- Module docstrings: multi-line, describe purpose and supported formats/backends
- Class docstrings: describe the class role and key parameters (NumPy-style for complex classes)
- Function docstrings: Parameters/Returns sections for public API functions
- Short private helpers: single-line docstring or none
- Usage examples in docstrings use `::` code blocks or `>>>` doctests

## Architectural Patterns

### Configuration (Single Source of Truth)
```python
from core.config import get_settings

settings = get_settings()  # lru_cache singleton — never instantiate Settings() directly
```
- All tunables live in `core/config.py` as `Settings` fields with `Field(default=...)`
- Env prefix: `AI_ASSISTANT_` — override any setting via environment variable
- Runtime overrides (TTS only) stored in module-level `_runtime_settings` with `threading.Lock`

### FastAPI Service Pattern
Each service follows this structure:
```python
app = FastAPI(title="Service Name", version="x.y.z")
app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)

class RequestModel(BaseModel): ...
class ResponseModel(BaseModel): ...

@app.post("/endpoint", response_model=ResponseModel)
async def endpoint(request: RequestModel) -> ResponseModel: ...

@app.get("/health")
async def health(): return {"status": "ok", "service": "name"}

if __name__ == "__main__":
    from uvicorn import run
    settings = get_settings()
    run(app, host=settings.X_host, port=settings.X_port, ...)
```

### Async + Threading Boundary
- FastAPI handlers are `async def`
- Blocking I/O (requests, file ops) offloaded with `asyncio.to_thread()`
- Thread-safety for shared mutable state: `threading.Lock()` or `threading.Event()`
- `asyncio.Lock()` for async-only shared state (e.g. `_tts_send_lock`)
- Never call blocking code directly in async handlers

### Cancellation / Interruption Pattern
Three-layer cancellation checked at every async boundary:
```python
def _is_cancelled() -> bool:
    if cancellation_event and cancellation_event.is_set():
        return True
    if interrupt_controller and interrupt_controller.is_triggered():
        return True
    if is_generation_current_fn and not is_generation_current_fn():
        return True
    return False
```
- `asyncio.Event` for async cancellation (pipeline-level)
- `InterruptController` (wraps `threading.Event`) for duplex barge-in
- `is_generation_current_fn` callable for stale-generation guard
- All three are optional parameters — callers pass only what they have

### Generation ID Pattern
Monotonically increasing integer passed through the entire call chain:
- `run_pipeline_streaming(generation_id=N)` → `stream_llm(generation_id=N)` → `stream_tts_from_tokens(generation_id=N)` → `/speak` payload `{"generation_id": N}`
- TTS service drops requests where `generation_id < _current_generation`
- New generation resets `_engine.reset_sequence()` and `_chunk_counter = 0`

### Streaming Pipeline Pattern
```python
async def token_iter():
    async for token in stream_llm(prompt, cancellation_event=..., generation_id=N):
        if _is_cancelled():
            break
        yield token

result = await stream_tts_from_tokens(token_iter(), ...)
```
- Producer/consumer via `asyncio.Queue[Optional[EmotionSegment]]`
- `None` sentinel signals end of queue
- Producer runs as `asyncio.create_task()`, consumer awaits queue items
- `finally` block always cancels producer task if interrupted

### Error Handling
- Broad `except Exception` with `# pylint: disable=broad-except` comment for non-fatal paths
- Fatal errors re-raise; non-fatal errors log and continue
- Service unavailability: log warning, return default/fallback value (never crash pipeline)
- HTTP errors: raise `HTTPException(status_code=502, ...)` with descriptive detail
- Timing always captured even on error paths (use `time.perf_counter()`)

### Structured Logging
All timing and stage logs use JSON format:
```python
logger.info(json.dumps({"stage": "llm_stream", "generation_id": N, "first_token_ms": 42.1}))
```
- Module-level logger: `logger = logging.getLogger(__name__)`
- `logging.basicConfig(level=logging.INFO)` only in service entry points
- Emoji prefixes for state transitions in pipeline logs (🎭 for emotion, ⚪🔵🟡🟢🔴 for states)
- `[service-name]` prefix in log messages for easy filtering: `[tts]`, `[llm]`, `[playback-engine]`

### Pydantic Models
- Request/response models are `BaseModel` subclasses defined near their endpoint
- Settings update models use `Optional[T] = None` for all fields (partial update pattern)
- Apply updates with `model_dump(exclude_none=True)` to skip unset fields:
  ```python
  for key, value in update.model_dump(exclude_none=True).items():
      current[key] = value
  ```
- Validate constraints manually in endpoint before constructing updated model

### State Machine Pattern
```python
class AssistantState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    ...

class AssistantStateController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = AssistantState.IDLE

    def set_state(self, state: AssistantState) -> None:
        with self._lock:
            self._state = state
```
- Enum for states with lowercase string values
- Controller class wraps enum with thread-safe lock
- Visual labels via dict mapping `{State: "emoji"}` for terminal feedback

### Memory / Embedding Pattern
```python
class MemoryManager:
    def __init__(self) -> None:
        sample_vector = embed("warmup")          # warm up model on init
        self.store = VectorStore(dim=len(sample_vector))

    def add_interaction(self, user_text: str, assistant_text: str) -> None: ...
    def retrieve(self, query: str, top_k: int = 3) -> List[str]: ...
    def format_memories(self, memories: List[str]) -> str: ...
```
- Warm up embedding model in `__init__` to avoid first-call latency
- `format_memories` returns `"(no long-term memories)"` string (never empty/None)
- Memory save is non-fatal — wrapped in try/except with warning log

### Dataclass for Value Objects
```python
@dataclass
class EmotionSegment:
    emotion: Optional[str]  # None means neutral
    text: str
```
- `@dataclass` for simple data containers (no methods needed)
- `@dataclass` + methods for streaming buffers (EmotionStreamBuffer)

### Lazy Stream Opening
```python
def enqueue(self, chunk_id: int, pcm: np.ndarray) -> None:
    if not self._active:
        self._open_stream()   # open audio device on first use
        self._active = True
    self._in_q.put((chunk_id, pcm))
```
- Audio device opened lazily on first `enqueue()`, not in `__init__`
- Graceful degradation when hardware unavailable (log error, set `_stream = None`)

## Testing Conventions
- Tests in `tests/` directory, one file per module/feature
- `conftest.py` for shared fixtures
- `monkeypatch` used to patch `_http_session.post` for TTS backend tests
- Test files named `test_<feature>.py`
- Run with: `.\venv\python.exe -m pytest tests -q`

## File Organization Rules
- One FastAPI `app` instance per service file
- Service files are runnable as `__main__` via uvicorn
- Helper functions that are only used within a module are prefixed `_`
- Section dividers use `# ── Section Name ──...` style (em-dash + spaces)
- Tunables grouped under `# ── Tunables ──` comment block in classes
