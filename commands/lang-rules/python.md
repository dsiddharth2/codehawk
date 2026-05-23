# Python Review Rules

## All Versions

### Security
- [ ] No hardcoded credentials, API keys, tokens, or connection strings — use environment variables or secrets manager
- [ ] Never log secrets, tokens, full connection strings, or PII — use redacted/hashed representations
- [ ] `os.system()` / `subprocess.run(shell=True)` not used with user-controlled input (command injection)
- [ ] SQL queries use parameterized form — no f-string or `%` formatting of query strings with user data
- [ ] File paths from user input sanitized — disallow `..`, `/`, `\` path separators; restrict to safe characters
- [ ] CSV export sanitizes fields starting with `=`, `+`, `-`, `@` to prevent formula injection
- [ ] Error messages do not expose raw exception details, connection strings, or internal paths to end users
- [ ] Redact URL query parameters containing credentials (`sig`, `code`, `token`, `sas`) before logging
- [ ] All HTTP requests use HTTPS — do not disable certificate verification

### Code Quality
- [ ] `except Exception` not used as a bare catch-all without re-raise or detailed logging
- [ ] Catch specific exceptions (`requests.RequestException`, `FileNotFoundError`, `json.JSONDecodeError`) not bare `except:`
- [ ] `logger.exception()` used in `except` blocks instead of `logger.error(str(e))` to capture stack traces
- [ ] Try/except blocks narrow in scope — only wrap code that can throw the caught exception
- [ ] Re-raise or raise a more specific exception with context if you catch for logging but cannot handle
- [ ] Mutable default arguments not used (`def f(items=[])`) — use `None` and initialize inside body
- [ ] Context managers (`with`) used for file handles, DB connections, locks — always clean up resources
- [ ] `isinstance()` used for type checks, not `type() ==`
- [ ] Generators preferred over list comprehensions for large sequences consumed once
- [ ] Circular imports avoided — modules organized so lower-level modules do not import higher-level ones
- [ ] `__all__` defined on public modules to control exported names
- [ ] Regular expressions compiled with `re.compile()` when used in a loop
- [ ] `dict.get(key, default)` used instead of direct indexing for optional or external data
- [ ] Thread-shared mutable state protected with `threading.Lock`
- [ ] Cyclomatic complexity kept low (under 10) — functions small and focused on a single task
- [ ] No unused variables, imports, or redundant code — remove dead code
- [ ] Distinct variable names for different responses — avoid copy-paste errors with wrong references
- [ ] No duplicate method definitions — remove shadowed methods

### Logging
- [ ] `logging` used instead of `print()` in library/production code
- [ ] Dedicated loggers per module (`logger = logging.getLogger(__name__)`) — not root logger with `logging.basicConfig()`
- [ ] Explicit `encoding='utf-8'` on file handlers
- [ ] Sanitize and truncate user inputs before logging at INFO level
- [ ] PII logged only at DEBUG level with truncation — never at INFO
- [ ] Hash identifiers before logging when correlation is needed but full value shouldn't be exposed

### Documentation & Type Hints
- [ ] All public modules, classes, and functions have docstrings (Google Style or NumPy Style)
- [ ] One-line docstrings end with a period (PEP 257)
- [ ] All function arguments and return values have type hints
- [ ] `Optional[T]` used for parameters that can be `None` — not bare `T` with default `None`
- [ ] `Dict[str, Any]`, `List[str]` used instead of bare `dict` or `list`
- [ ] Type hints match actual return values — don't annotate `str` if it can return `None`

### File I/O
- [ ] Always specify `encoding='utf-8'` when opening text files
- [ ] Wrap file operations in try/except for `FileNotFoundError`, `PermissionError`, `IOError`
- [ ] `Path.mkdir(parents=True, exist_ok=True)` before writing files
- [ ] `pathlib.Path` used instead of `os.path` string manipulation

### JSON & Data Parsing
- [ ] `json.load()` / `json.loads()` wrapped in try/except for `json.JSONDecodeError`
- [ ] Validate JSON structure after parsing — check required keys and expected types
- [ ] `.get()` with defaults used for optional or external data fields
- [ ] `if value is not None:` used instead of `if value:` when `0`, `""`, or `[]` are valid

### Network & HTTP
- [ ] ALL HTTP requests (`requests`, `httpx`, OpenAI client) have explicit `timeout=` parameter
- [ ] Retry logic with exponential backoff for transient errors (rate limits, connection errors)
- [ ] Success based on HTTP status codes (`response.status_code == 200`), not response body presence
- [ ] `response.json()` wrapped in try/except for `ValueError` / `json.JSONDecodeError`
- [ ] `POST` with `json=payload` for JSON bodies — not `GET` with JSON body
- [ ] `GET` uses `params=` for query parameters — not `data=` or `json=`

### Input Validation
- [ ] Validate all external inputs at function entry — check for `None`, empty strings, whitespace-only
- [ ] Strip and normalize string inputs with `.strip()` before validation
- [ ] `None` checked explicitly (`if value is None`) when empty strings are valid
- [ ] `isinstance()` checks for public API input types
- [ ] Validate lists by checking all items, not just the first one

### Async
- [ ] No blocking synchronous calls inside async functions — use `asyncio.run_in_executor()` or async clients
- [ ] `asyncio.to_thread()` for synchronous operations in async contexts
- [ ] `await` used for async tool invocations in agent frameworks
- [ ] `async def` and `await` used consistently when calling async APIs

### Performance
- [ ] `ThreadPoolExecutor` workers capped — use configurable limits, not `max_workers=len(items)`
- [ ] Avoid unnecessary `.copy()` calls on large objects
- [ ] Hoist loop-invariant computations outside loops
- [ ] Never mutate cached objects — return shallow copy when modifying cached data
- [ ] Cache keys use hashed representations of sensitive data, not raw values

### Configuration
- [ ] Deep merge (recursive) for nested config dicts — shallow `{**base, **override}` overwrites nested dicts
- [ ] `yaml.safe_load()` wrapped in try/except for `yaml.YAMLError`
- [ ] `os.getenv()` with sensible defaults — validate required env vars at startup
- [ ] Configurable thresholds instead of hardcoded magic numbers
- [ ] Non-interactive backends for Matplotlib (`matplotlib.use("Agg")`) before importing pyplot in CI

### Dependencies
- [ ] Optional dependency imports wrapped in try/except with helpful error messages
- [ ] `TYPE_CHECKING` for imports only needed for type hints — avoid runtime import errors
- [ ] `DataFrame.to_markdown()` wrapped in try/except (requires `tabulate`)
- [ ] Lazy imports for optional dependencies — import when needed, not at module level

### OpenAI & LLM Clients
- [ ] Set `timeout=` and `max_retries=` when creating OpenAI clients
- [ ] Catch `RateLimitError`, `APIConnectionError` and other client-specific exceptions
- [ ] Validate response structure before accessing fields — handle missing or malformed responses
- [ ] Use configuration-provided model names — don't hardcode model names

## Python 3.8+

- [ ] Walrus operator (`:=`) used for clarity, not in complex nested expressions that obscure control flow
- [ ] `typing` annotations present on all public function signatures
- [ ] `TypedDict` used for dict-typed parameters/returns rather than bare `Dict[str, Any]`
- [ ] `Protocol` used for structural subtyping instead of ABC when no state is shared

## Python 3.10+

- [ ] `match` / `case` (structural pattern matching) used for exhaustive variant dispatch
- [ ] Union types written as `X | Y` instead of `Union[X, Y]` in annotations
- [ ] `ParamSpec` used when decorators must preserve callable signatures

## Python 3.12+

- [ ] `type` statement used for type aliases instead of `TypeAlias` assignment
- [ ] `@override` decorator applied to methods that override base class implementations
- [ ] Generic classes use PEP 695 `class Foo[T]:` syntax rather than `Generic[T]` where supported
- [ ] `sys.monitoring` used for performance-sensitive profiling hooks instead of `sys.settrace`
- [ ] f-string nesting and complex expressions inside `{}` kept readable — extract to a variable if needed
- [ ] `ExceptionGroup` and `except*` used when multiple unrelated exceptions can be raised concurrently (e.g., in `asyncio.TaskGroup`)
- [ ] `dataclasses.field(default_factory=...)` used for mutable defaults in `@dataclass` fields
