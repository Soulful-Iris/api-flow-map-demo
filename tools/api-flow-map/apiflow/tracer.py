"""Turn a handler's statement tree into the flow an engineer would describe.

Per statement we decide, in this order:
1. Is it a response / early exit?          -> `return` or `throw` step with an HTTP outcome
2. Does it call into this repo?            -> `call` step, expanded into children (depth-limited)
3. Does it touch the outside world?        -> `io.db` / `io.http` / `io.queue` / `io.cache` / `io.file`
4. Is it validation, auth, or a transform? -> those kinds
5. Is it noise (getters, logging, plumbing)? -> dropped (logging kept as `log`, hidden by default)

Everything else becomes an `external` step only when it looks like business
logic (e.g. `riskEngine.score(order)`), so the flow stays readable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import labeler, lexer
from .discovery import Config
from .extractors.base import ClassDef, CodeIndex, EndpointSeed, FunctionDef, parse_params
from .httpcodes import EXCEPTION_STATUS, status_from_expr
from .model import Branch, Location, Step
from .redact import redact

# ---------------------------------------------------------------------------- patterns

_CALL_RX = re.compile(r"((?:[A-Za-z_$][\w$]*\s*(?:\?\.|\.|::|->)\s*)*[A-Za-z_$][\w$]*)\s*(?:<[^<>()]{0,80}>\s*)?\(")
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "function", "async", "await", "typeof", "super",
             "case", "else", "synchronized", "instanceof", "sizeof", "yield", "throw", "when", "select", "do", "try",
             "with", "lambda", "not", "and", "or", "in", "is", "def", "class", "elif", "except", "raise",
             "assert", "del", "import", "from", "as", "pass", "global", "nonlocal", "var", "let", "const", "func", "go",
             "defer", "range", "chan", "interface", "struct", "type"}
# Go builtins / python builtins: only noise when called bare (no receiver)
_BARE_NOISE = {"make", "len", "cap", "append", "copy", "delete", "panic", "recover", "close", "int", "string", "float", "bool", "byte",
               "rune", "error", "print", "println", "str", "list", "dict", "set", "tuple", "sorted", "enumerate", "zip", "range", "isinstance",
               "getattr", "setattr", "hasattr", "id", "hash", "iter", "next", "min", "max", "sum", "abs", "round", "any", "all", "map", "filter",
               "repr", "type", "super", "vars", "format", "open"}

RESPONSE_OBJECTS = {"res", "response", "reply", "rep", "ctx", "c", "w", "rw", "resp", "writer", "h", "context", "this.res", "self.response"}
RESPONSE_METHODS = {"status", "sendStatus", "json", "send", "end", "render", "redirect", "sendFile", "download", "jsonp", "code", "header",
                    "type", "view", "WriteHeader", "Write", "JSON", "IndentedJSON", "PureJSON", "SecureJSON", "String", "XML", "YAML", "Data",
                    "HTML", "Redirect", "Status", "NoContent", "AbortWithStatus", "AbortWithStatusJSON", "AbortWithError", "Blob", "File",
                    "Attachment", "Stream", "Text", "Bytes", "SendStatus", "SendString", "Response", "body", "text", "html", "notFound",
                    "badRequest", "created", "noContent", "accepted", "sendJSON", "Redirect", "Error", "abort", "throw", "ok", "fail"}
_STATUS_CARRIERS = {"status", "sendStatus", "code", "WriteHeader", "Status", "AbortWithStatus", "AbortWithStatusJSON", "JSON",
                    "IndentedJSON", "String", "XML", "Data", "HTML", "NoContent", "Redirect", "SendStatus", "Error", "abort", "Blob", "File"}
_IMPLICIT_200 = {"json", "send", "end", "render", "sendFile", "download", "jsonp", "view", "Write", "SendString", "Text", "Bytes", "body", "text", "html", "ok", "sendJSON"}

_JAVA_RESP_RX = re.compile(r"\b(?:ResponseEntity|Response|ServerResponse|HttpResponse|ResponseBuilder)\s*\.\s*(status|ok|notFound|badRequest|noContent|accepted|created|unprocessableEntity|internalServerError|of|seeOther|temporaryRedirect|serverError|unauthorized|forbidden|conflict)\s*\(([^)]*)\)")
_CS_RESP_RX = re.compile(r"\b(?:Results\.|TypedResults\.|return\s+|=>\s*|await\s+)?(Ok|NotFound|BadRequest|Created|CreatedAtAction|CreatedAtRoute|NoContent|Accepted|AcceptedAtAction|Conflict|Unauthorized|Forbid|UnprocessableEntity|StatusCode|Problem|ValidationProblem|Redirect|RedirectToAction|File|PhysicalFile|Json|Content|Challenge|SignIn|SignOut|Unauthorized|UnprocessableEntity)\s*\(([^)]*)")
_CS_STATUS = {"Ok": 200, "Json": 200, "Content": 200, "File": 200, "PhysicalFile": 200, "NotFound": 404, "BadRequest": 400, "Created": 201, "CreatedAtAction": 201,
              "CreatedAtRoute": 201, "NoContent": 204, "Accepted": 202, "AcceptedAtAction": 202, "Conflict": 409, "Unauthorized": 401,
              "Forbid": 403, "UnprocessableEntity": 422, "Problem": 500, "ValidationProblem": 400, "Redirect": 302, "RedirectToAction": 302,
              "Challenge": 401, "SignIn": 200, "SignOut": 200}
_JAVA_STATUS = {"ok": 200, "notFound": 404, "badRequest": 400, "noContent": 204, "accepted": 202, "created": 201, "unprocessableEntity": 422,
                "internalServerError": 500, "serverError": 500, "seeOther": 303, "temporaryRedirect": 307, "unauthorized": 401, "forbidden": 403, "conflict": 409}
_PY_RESP_RX = re.compile(r"\b(JsonResponse|HttpResponse|HttpResponseNotFound|HttpResponseBadRequest|HttpResponseForbidden|HttpResponseNotAllowed|HttpResponseRedirect|HttpResponseServerError|HttpResponseGone|Response|JSONResponse|PlainTextResponse|HTMLResponse|RedirectResponse|StreamingResponse|FileResponse|ORJSONResponse|jsonify|make_response|redirect|render_template|render|send_file|abort|RedirectResponse|StreamingHttpResponse)\s*\(")
_PY_STATUS = {"HttpResponseNotFound": 404, "HttpResponseBadRequest": 400, "HttpResponseForbidden": 403, "HttpResponseNotAllowed": 405,
              "HttpResponseRedirect": 302, "HttpResponseServerError": 500, "HttpResponseGone": 410, "redirect": 302, "RedirectResponse": 307}
_GO_ERR_RETURN_RX = re.compile(r"\b(err|errors\.New|fmt\.Errorf|Err[A-Z]\w*|status\.Error|status\.Errorf|echo\.NewHTTPError|fiber\.NewError|apierrors\.\w+|errs\.\w+)\b")
_EXC_CLASS_RX = re.compile(r"(?:new\s+|raise\s+|throw\s+)?\b([A-Z]\w*(?:Exception|Error|Fault|Failure|Denied|Timeout|NotFound|Invalid|Conflict|Forbidden|Unauthorized|Http404|Problem)\w*)\b")
_STRING_RX = re.compile(r"[\"'`]([^\"'`]{1,120})[\"'`]")

GENERIC_DB_RECEIVERS = re.compile(r"^(session|db|conn|connection|database|cursor|em|entityManager|tx|transaction|manager|context|dbContext|_context|_db|store|client|pool|jdbc|jdbcTemplate|template|mongo|mongoTemplate|table|collection|coll|knex|prisma|sequelize)$", re.I)
VIEW_PLUMBING_TYPES = {"Model", "ModelMap", "ModelAndView", "BindingResult", "RedirectAttributes", "HttpSession", "Errors", "WebRequest",
                       "UriComponentsBuilder", "Locale", "Principal", "Authentication", "Pageable", "Sort", "SessionStatus", "MultipartFile",
                       "HttpHeaders", "ServletUriComponentsBuilder", "Map", "ModelAttribute", "WebDataBinder", "Session"}
COLLABORATOR_TYPE_RX = re.compile(r"(Service|Client|Repository|Repo|Gateway|Manager|Handler|Provider|Facade|Api|Dao|Store|Adapter|Publisher|Producer|Factory|Helper|Util|Utils|Engine|Processor|Validator|Resolver|Scorer|Checker|Calculator|Orchestrator|Coordinator|Connector|Proxy|Registry|Dispatcher|Executor|Scheduler|Notifier|Sender|Mailer|Uploader|Downloader|Loader|Reader|Writer|Fetcher|Builder)$")
COLLECTION_TYPES = {"List", "ArrayList", "LinkedList", "Map", "HashMap", "LinkedHashMap", "TreeMap", "Set", "HashSet", "TreeSet", "Optional",
                    "String", "StringBuilder", "Integer", "Long", "Double", "Boolean", "BigDecimal", "LocalDate", "LocalDateTime", "Instant",
                    "Date", "UUID", "Collection", "Iterable", "Stream", "Array", "Object", "Number", "Promise", "Buffer", "Dictionary",
                    "IEnumerable", "IList", "ICollection", "Task", "Mono", "Flux", "CompletableFuture", "Iterator", "Pageable", "Page",
                    "Sort", "Duration", "Path", "File", "URI", "URL", "Matcher", "Pattern", "Random", "Thread", "Enum", "Exception",
                    "RuntimeException", "Error", "Function", "Supplier", "Consumer", "Runnable", "Callable", "Comparator", "Pair", "Tuple"}

NOISE_RECEIVERS = {"log", "logger", "LOGGER", "LOG", "Log", "console", "logging", "System", "Objects", "Optional", "Collections", "Arrays",
                   "String", "Math", "Integer", "Long", "Boolean", "Double", "UUID", "Instant", "LocalDate", "LocalDateTime", "ZonedDateTime",
                   "Duration", "Stream", "Collectors", "JSON", "Number", "Array", "Object", "Promise", "Date", "Symbol", "Reflect", "fmt",
                   "strconv", "strings", "time", "context", "os", "filepath", "bytes", "sort", "reflect", "utf8", "unicode", "regexp",
                   "url", "path", "util", "assert", "Mockito", "BigDecimal", "StringUtils", "Strings", "Lists", "Maps", "Sets", "Preconditions",
                   "MDC", "Thread", "TimeUnit", "Pattern", "Matcher", "Character", "Enum", "Class", "Files", "Paths", "Base64", "URLEncoder",
                   "URLDecoder", "Locale", "Currency", "ThreadLocalRandom", "Random", "SecureRandom", "Executors", "Comparator", "Function",
                   "Stream", "IntStream", "Mono", "Flux", "Try", "Either", "Validation", "Number", "parseInt", "parseFloat", "Buffer", "process",
                   "str", "int", "float", "bool", "list", "dict", "set", "tuple", "sorted", "enumerate", "zip", "range", "isinstance", "getattr",
                   "setattr", "hasattr", "datetime", "timedelta", "uuid", "json", "re", "math", "itertools", "functools", "typing", "copy", "dataclasses",
                   "decimal", "Decimal", "os", "sys", "traceback", "warnings", "Enumerable", "Convert", "Guid", "DateTime", "TimeSpan", "Task",
                   "Console", "Debug", "Trace", "Environment", "Path", "Uri", "Regex", "Encoding", "JsonSerializer", "JsonConvert", "Activator"}

NOISE_METHODS = {"toString", "equals", "hashCode", "builder", "build", "of", "ofNullable", "empty", "from", "valueOf", "parse", "format", "trim",
                 "toLowerCase", "toUpperCase", "isBlank", "isEmpty", "isPresent", "size", "length", "add", "addAll", "put", "putAll", "remove",
                 "contains", "containsKey", "keySet", "values", "entrySet", "stream", "collect", "map", "filter", "forEach", "toList", "toSet",
                 "toArray", "sorted", "distinct", "findFirst", "findAny", "anyMatch", "allMatch", "noneMatch", "count", "sum", "min", "max",
                 "orElse", "orElseGet", "get", "getOrDefault", "getOrElse", "ifPresent", "join", "split", "replace", "replaceAll", "substring",
                 "charAt", "indexOf", "startsWith", "endsWith", "matches", "concat", "append", "toJSON", "toJson", "stringify", "push", "pop",
                 "shift", "unshift", "slice", "splice", "reduce", "some", "every", "includes", "find", "findIndex", "flat", "flatMap", "keys",
                 "entries", "assign", "freeze", "isArray", "then", "catch", "finally", "resolve", "reject", "all", "allSettled", "race", "bind",
                 "call", "apply", "toFixed", "toISOString", "getTime", "now", "random", "floor", "ceil", "round", "abs", "pow", "sqrt", "trunc",
                 "block", "subscribe", "blockOptional", "log", "info", "debug", "warn", "error", "trace", "fatal", "Println", "Printf", "Sprintf",
                 "Errorf", "Fprintf", "Print", "Sprint", "Sprintln", "New", "Is", "As", "Unwrap", "Itoa", "Atoi", "Join", "Split", "Contains",
                 "HasPrefix", "HasSuffix", "ToLower", "ToUpper", "TrimSpace", "Background", "TODO", "WithTimeout", "WithCancel", "WithValue",
                 "Since", "Now", "Sleep", "Duration", "Value", "Err", "Done", "Getenv", "Exit", "Lock", "Unlock", "RLock", "RUnlock", "Add", "Wait",
                 "Error", "Unwrap", "Cause", "Message", "Errorf", "Wrap", "Wrapf", "WithMessage", "WithStack",
                 "requireNonNull", "checkNotNull", "getClass", "getName", "getSimpleName", "name", "ordinal", "compareTo", "toMap",
                 "collectList", "collectMap", "just", "defer", "fromIterable", "flux", "mono", "delayElement", "toBuilder", "copy",
                 "getMessage", "getCause", "getStackTrace", "printStackTrace", "getLogger", "getId", "getValue", "getKey", "getStatus",
                 "getBody", "getHeaders", "getStatusCode", "getData", "getResult", "getResults", "getContent", "getItems", "getList",
                 "isInstance", "cast", "clone", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "nextTick",
                 "encode", "decode", "hex", "utf8", "lower", "upper", "strip", "items", "update", "setdefault", "extend", "insert",
                 "isoformat", "strftime", "strptime", "utcnow", "today", "timestamp", "total_seconds", "dict", "list", "tuple", "str",
                 "len", "int", "float", "bool", "round", "sum", "sorted", "reversed", "enumerate", "zip", "any", "all", "print", "repr",
                 "hasattr", "getattr", "setattr", "isinstance", "issubclass", "type", "super", "vars", "id", "hash", "iter", "next",
                 "ToString", "Equals", "GetHashCode", "ToList", "ToArray", "ToDictionary", "Select", "Where", "First", "FirstOrDefault",
                 "Any", "Count", "Sum", "OrderBy", "OrderByDescending", "GroupBy", "Concat", "Distinct", "Skip", "Take", "ConfigureAwait",
                 "GetType", "Parse", "TryParse", "Format", "Trim", "Replace", "Substring", "IsNullOrEmpty", "IsNullOrWhiteSpace", "Add", "Remove",
                 "Contains", "ContainsKey", "TryGetValue", "Clear", "Cast", "OfType", "Aggregate", "Zip", "Reverse", "Max", "Min", "Average",
                 "Deserialize", "Serialize", "DeserializeObject", "SerializeObject", "WriteLine", "GetValueOrDefault", "HasValue",
                 "increment", "decrement", "recordTimer", "startTimer", "record", "gauge", "histogram", "observe", "timing", "tag", "tags",
                 "setAttribute", "setStatus", "addEvent", "recordException", "getOrCreate", "compute", "computeIfAbsent", "computeIfPresent",
                 "merge_", "peek", "limit", "skip", "boxed", "mapToInt", "mapToObj", "mapToLong", "mapToDouble", "iterator", "spliterator",
                 "asList", "unmodifiableList", "singletonList", "emptyList", "emptyMap", "singletonMap", "nCopies", "copyOf", "toUpperCase"}

BUSINESS_VERB_RX = re.compile(
    r"^(validate|process|calculate|compute|apply|enrich|verify|check|publish|send|notify|create|update|delete|remove|fetch|load|save|persist|"
    r"handle|build|generate|resolve|evaluate|assess|score|charge|refund|reserve|release|book|cancel|approve|reject|submit|dispatch|schedule|"
    r"trigger|sync|import|export|transform|convert|merge|aggregate|reconcile|settle|authorize|capture|void|issue|assign|allocate|route|"
    r"escalate|audit|record|track|emit|start|stop|run|execute|perform|do[A-Z]|try[A-Z]|find|search|lookup|query|list|register|enroll|"
    r"activate|deactivate|lock|unlock|encrypt|decrypt|sign|hash|tokenize|mask|redact|upsert|order|ship|pay|bill|invoice|price|quote|"
    r"rate|limit|throttle|retry|recover|rollback|commit|begin|open|close|connect|disconnect|login|logout|authenticate|refresh|revoke|"
    r"grant|deny|block|unblock|flag|review|inspect|analyze|classify|detect|match|compare|select|pick|choose|decide|determine|"
    r"prepare|initialize|init|setup|configure|reset|clear|purge|archive|restore|backup|migrate|transfer|move|copy|clone|"
    r"forward|redirect|proxy|call|invoke|request|respond|reply|answer|ack|confirm|complete|finish|finalize|close|abort|"
    r"suspend|resume|pause|wait|await|poll|watch|monitor|measure|estimate|predict|forecast|recommend|suggest|rank|sort|"
    r"group|partition|split|combine|join|link|attach|detach|bind|unbind|subscribe|unsubscribe|mark|set[A-Z]\w+(?:Status|State|Flag)|"
    r"add[A-Z]|increment|decrement|adjust|debit|credit|deposit|withdraw|transfer|post|get[A-Z]\w*(?:For|By|From|With|Of)|ensure|assert|"
    r"require|guard|protect|sanitize|normalize|format|render|compose|assemble|parse|read|write|store|cache|index|lookup|"
    r"resolve|translate|localize|map[A-Z]|to[A-Z]|from[A-Z]|of[A-Z])")

IO_PATTERNS: Dict[str, List[str]] = {
    "io.db": [r"(Repository|Repositories|Repo|Dao|DAO|JdbcTemplate|NamedParameterJdbcTemplate|EntityManager|SessionFactory|MongoTemplate|MongoOperations|DynamoDb|DynamoDB|dynamodb|Dynamo|JpaRepository|CrudRepository|R2dbc|DatabaseClient|Datastore|QueryBuilder|Criteria|TypedQuery|jdbc|Jdbc|Hibernate|Jooq|jooq|dsl\.|DSLContext|cassandra|Cassandra|Neo4j|Elasticsearch|elasticsearch|OpenSearch|prisma|knex|sequelize|mongoose|typeorm|drizzle|objection|bookshelf|[A-Z]\w+Model\.(find|create|update|delete|insert|save|aggregate|count|findOne|findById|findAll|bulkWrite|updateOne|deleteOne|upsert|query|where)|\.objects\.|session\.(query|add|delete|merge|get|execute|commit|rollback|flush|refresh|scalar|scalars)|db\.|database\.|pool\.|conn\.|connection\.|client\.(query|execute|batch)|cursor\.|collection\.|coll\.|table\.|Table\.|dynamo|firestore|Firestore|bigtable|redshift|snowflake|gorm|sqlx|sqlc|pgx|\.Exec\(|\.ExecContext\(|\.Query\(|\.QueryRow\(|\.QueryContext\(|\.QueryRowContext\(|\.Begin\(|\.BeginTx\(|\.Prepare\(|\.PrepareContext\(|Transaction|transaction|withTransaction|\$transaction|atomic\(|get_object_or_404|get_list_or_404|select_related|prefetch_related|bulk_create|save\(\)|\.delete\(\)|\.refresh_from_db|dbContext|DbContext|_context\.|_db\.|DbSet|SaveChanges|ToListAsync|FirstOrDefaultAsync|FindAsync|SingleOrDefaultAsync|AnyAsync|ExecuteSql|FromSql|IQueryable|Dapper|QueryAsync|ExecuteAsync|QueryFirst|\.Include\(|mapper\.(select|insert|update|delete)|Mapper\.(select|insert|update|delete)|MyBatis|sqlSession|SqlSession)", ],
    "io.http": [r"(RestTemplate|restTemplate|WebClient|webClient|HttpClient|httpClient|OkHttp|okHttp|Feign|feign|Retrofit|retrofit|axios|fetch\(|got\(|superagent|request\(|requests\.|httpx|aiohttp|urllib|urlopen|http\.(Get|Post|Put|Do|NewRequest|Head|PostForm)|client\.Do\(|resty|HttpRequest|WebTarget|Invocation|RestClient|restClient|graphql|GraphQL|apollo|grpc|Grpc|Stub\b|stub\.|Client\.(Get|Post|Put|Delete|Call|Invoke|Send|Fetch|get|post|put|patch|delete|request|send|fetch|stream|head|options)|Session\.(get|post|put|patch|delete|request)|IHttpClientFactory|HttpClientFactory|GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync|PostAsJsonAsync|PutAsJsonAsync|GetFromJsonAsync|Refit|Flurl|\.get\(\s*['\"`]https?://|\.post\(\s*['\"`]https?://|ky\.|needle|undici|ServiceClient|serviceClient|Gateway|gateway|Connector|connector|Adapter\.(get|post|put|delete|call|fetch|send)|apiClient|ApiClient|Api\.|api\.(get|post|put|patch|delete)|soap|Soap|SOAP|wsdl|lambdaClient|LambdaClient|\.invoke\(|Invoke\()"],
    "io.queue": [r"(KafkaTemplate|kafkaTemplate|kafka|Kafka|Producer\b|producer\.(send|produce|publish)|SqsClient|sqsClient|sqs\.|SQS|SnsClient|snsClient|sns\.|SNS|RabbitTemplate|rabbitTemplate|rabbit|Rabbit|amqp|JmsTemplate|jmsTemplate|jms|Jms|EventBridge|eventBridge|eventbridge|Publisher\b|publisher\.|publish\(|emit\(|sendMessage|SendMessage|sendEvent|PubSub|pubsub|topic\.|Topic\.|queue\.|Queue\.|MessageChannel|StreamBridge|streamBridge|ApplicationEventPublisher|eventPublisher|eventBus|EventBus|bus\.(send|publish|emit)|Kinesis|kinesis|nats\.|natsConn|celery|\.delay\(|apply_async|send_task|MessageProducer|ServiceBus|serviceBus|IBus|_bus\.|Mediator|_mediator\.|mediator\.(Send|Publish)|ActiveMQ|Pulsar|pulsar|Outbox|outbox|EventStore|eventStore|dispatchEvent|Dispatcher\.|dispatcher\.|RedisPub|MQ\b|mq\.|Broker|broker\.)"],
    "io.cache": [r"(RedisTemplate|redisTemplate|redis|Redis|cache|Cache|memcached|Memcache|Jedis|jedis|Lettuce|CacheManager|caffeine|Caffeine|lru|LRU|IDistributedCache|IMemoryCache|_cache\.|cacheClient|hazelcast|Hazelcast|ignite|Ignite|ehcache|Ehcache|elasticache)"],
    "io.file": [r"(S3Client|s3Client|s3\.|S3\b|Files\.(read|write|copy|move|delete|exists|createDirectory|newBufferedReader|newBufferedWriter|lines|list|walk)|FileSystem|fs\.(readFile|writeFile|readFileSync|writeFileSync|unlink|mkdir|createReadStream|createWriteStream|promises)|os\.(Open|Create|ReadFile|WriteFile|Remove|MkdirAll)|ioutil\.|io\.ReadAll|open\(|shutil\.|GCS|gcs|storage\.(bucket|Bucket|upload|download)|Blob\b|blob\.|BlobClient|blobClient|FileInputStream|FileOutputStream|FileReader|FileWriter|MultipartFile|multipart|UploadFile|uploadFile|downloadFile|getObject|putObject|GetObject|PutObject|upload\(|download\(|Storage\.|Bucket\.|bucket\.|sftp|Sftp|ftp\.|Ftp)"],
}
VALIDATE_RX = re.compile(r"(valid|Valid|validate|Validate|Validator|validator|check|Check|assert|Assert|require|Require|ensure|Ensure|verify|Verify|sanitize|Sanitize|schema\.(parse|validate|safeParse)|Schema\.(parse|validate)|parseBody|parseParams|parseQuery|throwIfInvalid|Preconditions|ModelState|IsValid|isValid|Joi\.|zod|yup|ajv|celebrate|matches\(|Pattern\.|ShouldBind|BindJSON|ShouldBindJSON|MustBind|Bind\(|BindAndValidate|Validation|validation|TryValidateModel|FluentValidation|_validator|validateOrReject|validateSync|validateAsync|plainToInstance|guard\()")
AUTH_RX = re.compile(r"(auth|Auth|authorize|authorise|authenticate|permission|Permission|hasRole|hasPermission|hasAuthority|isAuthorized|isAuthenticated|checkAccess|checkPermission|SecurityContext|getPrincipal|Principal|jwt|Jwt|JWT|token|Token(?!izer)|session\.(user|get)|acl|Acl|ACL|policy|Policy|allowed|canAccess|assertOwner|verifyOwner|requireRole|requireUser|currentUser|current_user|getCurrentUser|get_current_user|loginRequired|login_required|passport|oauth|OAuth|openid|OpenID|saml|Saml|kerberos|apiKey|api_key|ApiKey|credential|Credential|password|Password|secret\b|Secret\b|encrypt|decrypt|sign\(|verify\(|Claims|claims|Identity|identity|IsInRole|User\.(Identity|IsInRole|Claims|FindFirst)|HttpContext\.User|ClaimsPrincipal|_authorizationService|AuthorizeAsync|GetUserAsync|UserManager|SignInManager|has_perm|has_perms|check_permissions|check_object_permissions|IsAuthenticated|IsAdminUser|permission_classes|AccessControl|access_control|entitlement|Entitlement|scope|Scope|role|Role|tenant|Tenant|impersonat|Impersonat|mfa|MFA|otp|OTP|captcha|Captcha|csrf|CSRF|xsrf|cors|Cors)")
LOG_RX = re.compile(r"^(log|logger|LOGGER|LOG|Log|console|logging|slog|zap|logrus|klog|metrics|Metrics|meter|Meter|meterRegistry|registry|tracer|Tracer|span|Span|telemetry|Telemetry|stats|statsd|counter|Counter|histogram|Histogram|timer|Timer|newrelic|NewRelic|datadog|Datadog|ddtrace|MDC|Sentry|sentry|apm|APM|otel|opentelemetry|monitoring|Monitoring|_logger|_log|this\.logger|this\.log|self\.logger|self\.log|logging\.getLogger|structlog|glog|xlog|trace|Trace|debug|Debug|audit|auditLog|auditLogger|_telemetry|telemetryClient|TelemetryClient|Activity|activitySource|ActivitySource)\b")
TRANSFORM_RX = re.compile(r"(Mapper|mapper|map(To|From)[A-Z]|toDto|toDTO|toEntity|toDomain|toModel|toResponse|toResource|toView|toVo|toVO|toPojo|toRecord|from(Dto|DTO|Entity|Domain|Model|Request)|convert|Convert|Converter|converter|assemble|Assembler|assembler|transform|Transform|normalize|Normalize|enrich|Enrich|build(Response|Request|Dto|Entity|Model)|BeanUtils\.copyProperties|ModelMapper|modelMapper|AutoMapper|_mapper|Mapper\.Map|\.Map<|serializer|Serializer|Adapt|adapt|Translate|translate|Hydrate|hydrate|NewDecoder|get_json|ReadFrom|ParseForm|FormValue)")
ASYNC_RX = re.compile(r"(executor|Executor|CompletableFuture\.(runAsync|supplyAsync)|ForkJoinPool|Thread\(|new\s+Thread|go\s+func|setImmediate|setTimeout|process\.nextTick|queueMicrotask|asyncio\.(create_task|gather|ensure_future|run_in_executor)|Task\.Run|Task\.Factory|Parallel\.|ThreadPool|BackgroundJob|backgroundJob|Hangfire|Quartz|@Async|taskExecutor|scheduler|Scheduler|\.schedule\(|background_tasks\.add_task|BackgroundTasks|worker\.|Worker\.|job\.|Job\.|celery)")
FLAG_RX = labeler._FLAG_RX
REQUEST_PARSE_RX = re.compile(r"^(req|request|ctx|c|r|event|body|form|params|query|payload|input|data)\.(body|json|get_json|form|data|text|param|query|header|headers|get|ShouldBindJSON|BindJSON|ShouldBind|Bind|ShouldBindUri|ShouldBindQuery|ParseForm|FormValue|FormFile|PostForm|Param|Query|GetHeader|MultipartForm|Cookie|json\(\)|text\(\)|formData|arrayBuffer|parseBody|params)\b")
GETTER_RX = re.compile(r"^(get|is|has|to|as|with|set)[A-Z]\w*$")


def _compile_list(patterns: List[str]) -> List[re.Pattern]:
    out = []
    for p in patterns:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


@dataclass
class _Ctx:
    endpoint_lang: str
    budget: int
    default_status: Optional[int] = None
    return_type: str = ""
    steps_made: int = 0
    truncated: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class _Scope:
    fn: FunctionDef
    types: Dict[str, str] = field(default_factory=dict)
    pending_status: Optional[int] = None
    owner: Optional[ClassDef] = None
    package: str = ""
    catch_stack: List[str] = field(default_factory=list)
    return_type: str = ""
    consts: Dict[str, str] = field(default_factory=dict)


class Tracer:
    def __init__(self, index: CodeIndex, config: Config):
        self.index = index
        self.cfg = config
        self.io_rx: Dict[str, List[re.Pattern]] = {k: _compile_list(v) for k, v in IO_PATTERNS.items()}
        for kind, pats in (config.io_patterns or {}).items():
            self.io_rx.setdefault(kind, []).extend(_compile_list(list(pats)))
        self.noise_rx = _compile_list(config.noise_patterns or [])
        self.auth_rx = _compile_list(config.auth_patterns or [])
        self.validation_rx = _compile_list(config.validation_patterns or [])
        self.flag_rx = _compile_list(config.feature_flag_patterns or [])
        self._stmt_cache: Dict[int, List[lexer.Stmt]] = {}

    # ------------------------------------------------------------------ entry
    def trace(self, seed: EndpointSeed) -> List[Step]:
        ep = seed.endpoint
        fn = seed.fn
        if fn is None:
            return []
        ctx = _Ctx(endpoint_lang=fn.lang, budget=self.cfg.max_steps_per_endpoint, default_status=ep.properties.get("status"),
                   return_type=str(ep.properties.get("returns", "") or ""))
        try:
            steps = self._trace_fn(fn, 0, {id(fn)}, ctx, seed.owner_class)
        except RecursionError:
            ep.warnings.append("trace aborted: nesting too deep")
            steps = []
        except Exception as exc:  # never let one endpoint break the run
            ep.warnings.append(f"trace failed: {type(exc).__name__}: {exc}")
            steps = []
        if ctx.truncated:
            ep.warnings.append(f"flow truncated at {self.cfg.max_steps_per_endpoint} steps (raise max_steps_per_endpoint in .apiflow.json)")
        ep.warnings.extend(ctx.warnings)
        if not steps and fn is not None and fn.has_body:
            steps = [Step(kind="note", label="No traceable steps (handler body is trivial or could not be parsed)", detail="",
                          location=Location(fn.file.path, fn.line))]
        # observed responses
        codes = sorted({s.outcome["status"] for s in _iter(steps) if s.outcome.get("status")})
        if codes:
            ep.properties["responses"] = codes
        flags = sorted({t for s in _iter(steps) for t in s.tags if t.startswith("flag:")})
        if flags:
            ep.properties["feature_flags"] = [f[5:] for f in flags]
        return steps

    # ------------------------------------------------------------------ functions
    def _stmts(self, fn: FunctionDef) -> List[lexer.Stmt]:
        key = id(fn)
        if key in self._stmt_cache:
            return self._stmt_cache[key]
        if fn.lang == "python":
            from .extractors.python_web import py_to_stmts
            stmts = py_to_stmts(getattr(fn.py_node, "body", []) or [])
        else:
            sf = fn.file
            stmts = lexer.parse_body(sf.text, sf.masked, sf.lang, fn.start, fn.end, sf.lines)
        self._stmt_cache[key] = stmts
        return stmts

    def _scope_for(self, fn: FunctionDef, owner: Optional[ClassDef]) -> _Scope:
        sc = _Scope(fn=fn, owner=owner, package=fn.package)
        if owner is None and fn.owner:
            owner = self.index.class_named(fn.owner, fn.file.path)
            sc.owner = owner
        if owner:
            sc.types.update(owner.fields)
        for _ann, ptype, pname in parse_params(fn.lang, fn.params or ""):
            t = re.sub(r"<.*", "", ptype or "").replace("*", "").replace("[]", "").strip().split(".")[-1].rstrip("?")
            if t:
                sc.types[pname] = t
        if fn.lang == "go":
            for m in re.finditer(r"([A-Za-z_]\w*)\s+\*?(?:[\w]+\.)?([A-Z]\w*)", fn.params or ""):
                sc.types[m.group(1)] = m.group(2)
        # module-level var hints (JS/Python/Go)
        sc.types.update({k: v for k, v in self.index.module_vars.get(fn.file.path, {}).items() if k not in sc.types})
        sc.consts = dict(self.index.constants.get(fn.file.path, {}))
        for local, (tfile, name) in self.index.imports.get(fn.file.path, {}).items():
            if tfile and name in self.index.constants.get(tfile, {}):
                sc.consts.setdefault(local, self.index.constants[tfile][name])
        sc.return_type = _declared_return_type(fn)
        return sc

    def _trace_fn(self, fn: FunctionDef, depth: int, path: Set[str], ctx: _Ctx, owner: Optional[ClassDef] = None) -> List[Step]:
        stmts = self._stmts(fn)
        scope = self._scope_for(fn, owner)
        return self._convert(stmts, scope, depth, path, ctx, in_branch=False)

    # ------------------------------------------------------------------ statements
    def _convert(self, stmts: List[lexer.Stmt], scope: _Scope, depth: int, path: Set[str], ctx: _Ctx, in_branch: bool) -> List[Step]:
        out: List[Step] = []
        for st in stmts:
            if ctx.steps_made >= ctx.budget:
                if not ctx.truncated:
                    ctx.truncated = True
                    out.append(Step(kind="note", label="… flow truncated (step limit reached)", detail=""))
                break
            self._learn_types(st, scope)
            k = st.kind
            if k == "block":
                out.extend(self._convert(st.body, scope, depth, path, ctx, in_branch))
            elif k == "if" or k == "switch":
                step = self._branch(st, scope, depth, path, ctx)
                init = getattr(st, "init_text", "")
                if init:
                    init_st = lexer.Stmt("expr", init, st.line, pos=0)
                    out.extend(self._calls_in(init_st, scope, depth, path, ctx))
                if "empty" in step.tags:
                    ctx.steps_made -= 1
                    continue   # a decision with nothing traceable inside adds noise, not insight
                out.append(step)
            elif k == "try":
                out.append(self._try(st, scope, depth, path, ctx))
            elif k == "loop":
                out.append(self._loop(st, scope, depth, path, ctx))
            elif k == "return":
                out.extend(self._return(st, scope, depth, path, ctx, in_branch))
            elif k == "throw":
                out.extend(self._throw(st, scope, depth, path, ctx))
            else:
                out.extend(self._expr(st, scope, depth, path, ctx, in_branch))
        return _merge_header_writes(out)

    def _learn_types(self, st: lexer.Stmt, scope: _Scope) -> None:
        t = st.text
        if not t or st.kind not in ("expr",):
            return
        lang = scope.fn.lang
        if lang in ("java", "csharp"):
            m = re.match(r"^(?:final\s+|var\s+)?([A-Z][\w.]*)(?:<[^>]*>)?\s+([a-z_$][\w$]*)\s*=", t)
            if m:
                scope.types[m.group(2)] = m.group(1).split(".")[-1]
                return
            m = re.match(r"^(?:final\s+)?var\s+([a-z_$][\w$]*)\s*=\s*new\s+([A-Z][\w.]*)", t)
            if m:
                scope.types[m.group(1)] = m.group(2).split(".")[-1]
        elif lang == "kotlin":
            m = re.match(r"^(?:val|var)\s+([a-z_][\w]*)\s*(?::\s*([A-Z][\w.<>?]*))?\s*=\s*(?:([A-Z][\w.]*)\s*\()?", t)
            if m:
                ty = m.group(2) or m.group(3)
                if ty:
                    scope.types[m.group(1)] = re.sub(r"[<?].*", "", ty).split(".")[-1]
        elif lang in ("typescript", "javascript"):
            m = re.match(r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*([A-Z][\w.<>]*))?\s*=\s*(?:await\s+)?(?:new\s+([A-Z][\w.]*))?", t)
            if m:
                ty = m.group(2) or m.group(3)
                if ty:
                    scope.types[m.group(1)] = re.sub(r"<.*", "", ty).split(".")[-1]
        elif lang == "go":
            m = re.match(r"^([A-Za-z_]\w*)\s*(?:,\s*\w+\s*)?:?=\s*(?:&\s*)?(?:[\w]+\.)?(?:New)?([A-Z]\w*)\s*[({]", t)
            if m:
                scope.types[m.group(1)] = m.group(2)
        elif lang == "python":
            m = re.match(r"^([a-z_]\w*)\s*(?::\s*([A-Z]\w*))?\s*=\s*(?:[\w.]+\.)?([A-Z]\w*)\s*\(", t)
            if m:
                scope.types[m.group(1)] = m.group(2) or m.group(3)
            for wm in re.finditer(r"(?:[\w.]+\.)?([A-Z]\w*)\([^)]*\)\s+as\s+([a-z_]\w*)", t):
                scope.types[wm.group(2)] = wm.group(1)

    # ------------------------------------------------------------------ control flow
    def _branch(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx) -> Step:
        lang = scope.fn.lang
        branches: List[Branch] = []
        if lang == "go" and st.kind == "if" and st.arms and ";" in st.arms[0].cond:
            parts = lexer.split_top_level(st.arms[0].cond, ";")
            if len(parts) == 2:
                init, cond = parts[0].strip(), parts[1].strip()
                st.arms[0].cond = cond
                st.cond = cond
                if init:
                    st.init_text = init
        for arm in st.arms:
            steps = self._convert(arm.body, scope, depth, path, ctx, in_branch=True)
            b = Branch(label=labeler.branch_label(arm.label, arm.cond, lang, st.cond if st.kind == "switch" else ""),
                       condition=arm.cond, steps=steps, exits=_arm_exits(steps))
            branches.append(b)
        cond = st.cond
        if lang == "go" and st.kind == "if" and len(branches) == 1 and re.fullmatch(r"\s*err\s*!=\s*nil\s*", cond or "") \
                and len(branches[0].steps) == 1 and branches[0].steps[0].kind == "throw":
            inner = branches[0].steps[0]
            inner.label = "On error, fail" if inner.label == "Fail with the error" else "On error, " + inner.label[0].lower() + inner.label[1:]
            inner.tags = list(dict.fromkeys(inner.tags + ["error-propagation", "guard"]))
            inner.condition = cond
            return inner
        if st.kind == "switch":
            label = labeler.sentence("depending on " + labeler._humanize_expr(re.sub(r"\.get(\w+)\(\)", lambda m: "." + m.group(1)[0].lower() + m.group(1)[1:], cond)))
            detail = f"switch ({cond})"
        else:
            label = branches[0].label if branches else "If …"
            detail = f"if ({cond})"
        step = Step(kind="branch", label=label, detail=detail, condition=cond, branches=branches,
                    location=Location(scope.fn.file.path, st.line), code=self._code(st.text or detail))
        if labeler.is_feature_flag(cond) or any(rx.search(cond) for rx in self.flag_rx):
            step.tags.append("feature-flag")
            fm = re.search(r"['\"]([\w.\-:/]+)['\"]", cond)
            if fm:
                step.tags.append("flag:" + fm.group(1))
        if not any(b.steps for b in branches) and not (labeler.is_feature_flag(cond) or AUTH_RX.search(cond) or VALIDATE_RX.search(cond)):
            step.tags.append("empty")
        if any(b.exits for b in branches) and not all(b.exits for b in branches):
            step.tags.append("early-exit")
        if any(b.exits for b in branches) and len(branches) == 1:
            step.tags.append("guard")
        if AUTH_RX.search(cond) or any(rx.search(cond) for rx in self.auth_rx):
            step.tags.append("auth-check")
        elif VALIDATE_RX.search(cond) or any(rx.search(cond) for rx in self.validation_rx):
            step.tags.append("validation")
        ctx.steps_made += 1
        return step

    def _try(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx) -> Step:
        lang = scope.fn.lang
        branches: List[Branch] = []
        for arm in st.arms:
            if arm.label.startswith("catch"):
                scope.catch_stack.append(arm.cond)
            steps = self._convert(arm.body, scope, depth, path, ctx, in_branch=(arm.label != "try"))
            if arm.label.startswith("catch"):
                scope.catch_stack.pop()
            branches.append(Branch(label=labeler.branch_label(arm.label, arm.cond, lang), condition=arm.cond, steps=steps, exits=_arm_exits(steps)))
        caught = [b.condition for b in branches if b.label.startswith("On ")]
        names = [(labeler.error_words(c.split("|")[0].strip()) if not re.match(r"^[a-z_]\w*$", c.split("|")[0].strip()) else "") or "errors" for c in caught]
        label = "Try; on " + ", ".join(names) + " handle it" if caught else "Try"
        if caught:
            label = "Try, handling " + ", ".join(names)
        if len(label) > 90:
            label = "Try, handling errors"
        step = Step(kind="try", label=labeler.sentence(label), detail="try/catch", branches=branches,
                    location=Location(scope.fn.file.path, st.line), code=self._code(st.arms[0].cond) if st.arms and st.arms[0].cond else "")
        ctx.steps_made += 1
        return step

    def _loop(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx) -> Step:
        lang = scope.fn.lang
        children = self._convert(st.body, scope, depth, path, ctx, in_branch=True)
        step = Step(kind="loop", label=labeler.loop_label(st.text, st.cond, lang), detail=f"{st.text} ({st.cond})".strip(),
                    condition=st.cond, children=children, location=Location(scope.fn.file.path, st.line), code=self._code(st.cond))
        ctx.steps_made += 1
        return step

    # ------------------------------------------------------------------ exits
    def _return(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx, in_branch: bool) -> List[Step]:
        lang = scope.fn.lang
        text = st.text or ""
        out: List[Step] = []
        if not text.strip():
            return out   # bare `return` after an explicit response write: nothing new happens
        status, throws = self._outcome_of(text, lang, scope)
        # calls made while building the response (e.g. return service.process(x))
        resp_chain = self._is_response_expr(text, lang)
        calls = self._calls_in(st, scope, depth, path, ctx, skip_response=True)
        out.extend(calls)
        for lb in st.lambdas:
            out.extend(self._convert(lb, scope, depth, path, ctx, in_branch))
        if throws:
            step = Step(kind="throw", label=labeler.throw_label(throws, status, _first_string(text)), detail=text[:160], outcome={"throws": throws},
                        location=Location(scope.fn.file.path, st.line), code=self._code(text))
            if status:
                step.outcome["status"] = status
            out.append(step)
            ctx.steps_made += 1
            return out
        if lang == "go" and depth > 0 and _GO_ERR_RETURN_RX.search(text) and not resp_chain:
            plain_err = bool(re.fullmatch(r"(?:[\w.*&{}\[\]]+\s*,\s*)*err", text.strip()))
            if plain_err and not in_branch:
                return out   # `return result, err` at the end of a function: the normal exit
            exc = _go_error_name(text)
            label = labeler.throw_label(exc, None, "" if " " in exc else _first_string(text))
            if plain_err:
                label = "Fail with the error"
            step = Step(kind="throw", label=label, detail="return " + text[:140],
                        outcome={"throws": exc}, location=Location(scope.fn.file.path, st.line), code=self._code("return " + text))
            out.append(step)
            ctx.steps_made += 1
            return out
        if lang == "go" and depth > 0 and re.fullmatch(r"nil(?:\s*,\s*nil)*", text.strip()):
            if in_branch:
                step = Step(kind="return", label="Return nothing", detail="return " + text, location=Location(scope.fn.file.path, st.line),
                            code=self._code("return " + text), tags=["early-exit"])
                out.append(step)
                ctx.steps_made += 1
            return out
        if status is None and scope.pending_status:
            status = scope.pending_status
        if status is None and depth == 0:
            status = ctx.default_status or (302 if re.search(r"\bredirect\b", text, re.I) else 200)
        if depth == 0 or in_branch or status is not None:
            if depth > 0 and not in_branch and status is None:
                return out
            rtype = scope.return_type or (ctx.return_type if depth == 0 else "")
            label = labeler.return_label(status, text, depth, lang, rtype)
            step = Step(kind="return", label=label, detail=("return " + text[:140]).strip(), outcome={"status": status} if status else {},
                        location=Location(scope.fn.file.path, st.line), code=self._code("return " + text))
            if depth > 0 and status is None:
                step.tags.append("early-exit")
            out.append(step)
            ctx.steps_made += 1
        return out

    def _throw(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx) -> List[Step]:
        lang = scope.fn.lang
        text = st.text or ""
        out: List[Step] = []
        out.extend(self._calls_in(st, scope, depth, path, ctx, skip_response=True, skip_exceptions=True))
        exc = _exception_name(text, lang)
        rethrow = False
        if re.match(r"^[a-z_]\w*$", text.strip() or "") or not text.strip():
            # `throw e` / bare `raise` inside a catch: rethrow of the caught type
            if scope.catch_stack:
                exc = (scope.catch_stack[-1].split("|")[0].strip().split(" ")[0] or exc).split(".")[-1]
                rethrow = True
        status = self._status_for_exception(exc, text)
        label = labeler.throw_label(exc, status, _first_string(text))
        if rethrow:
            label = "Rethrow " + (labeler.error_words(exc) or "the error")
        step = Step(kind="throw", label=label, detail=("throw " if lang != "python" else "raise ") + text[:140],
                    outcome={"throws": exc} if exc else {}, location=Location(scope.fn.file.path, st.line), code=self._code(text))
        if status:
            step.outcome["status"] = status
        if not exc and lang == "go":
            step.label = "Panic"
        out.append(step)
        ctx.steps_made += 1
        return out

    def _status_for_exception(self, exc: str, text: str) -> Optional[int]:
        s = status_from_expr(text)
        if s:
            return s
        if exc in self.index.exception_status:
            return self.index.exception_status[exc]
        if exc in EXCEPTION_STATUS:
            return EXCEPTION_STATUS[exc]
        low = exc.lower()
        for key, code in (("notfound", 404), ("unauthori", 401), ("forbidden", 403), ("permission", 403), ("accessdenied", 403),
                          ("conflict", 409), ("duplicate", 409), ("badrequest", 400), ("validation", 400), ("invalid", 400),
                          ("illegalargument", 400), ("unprocessable", 422), ("toomany", 429), ("ratelimit", 429), ("timeout", 504),
                          ("unavailable", 503), ("notimplemented", 501), ("gone", 410)):
            if key in low:
                return code
        return None

    def _outcome_of(self, text: str, lang: str, scope: _Scope) -> Tuple[Optional[int], str]:
        """(status, exception) carried by a return expression."""
        t = text
        if not t:
            return None, ""
        throws = ""
        m = re.search(r"\b(?:Mono|Flux)\.error\(\s*(?:new\s+)?([A-Z]\w*)", t)
        if m:
            throws = m.group(1)
            return self._status_for_exception(throws, t), throws
        if lang in ("java", "kotlin"):
            statuses: List[int] = []
            for m in _JAVA_RESP_RX.finditer(t):
                meth, args = m.group(1), m.group(2)
                if meth == "status":
                    s = status_from_expr(args)
                    if s:
                        statuses.append(s)
                elif meth == "of":
                    statuses.append(200)
                elif meth in _JAVA_STATUS:
                    statuses.append(_JAVA_STATUS[meth])
            if statuses:
                return statuses[0], ""
            m = re.search(r"\bResponse\.Status\.([A-Z_]+)", t)
            if m:
                return status_from_expr(m.group(1)), ""
            if re.search(r"\bnew\s+ResponseEntity<?[^(]*\(", t):
                s = status_from_expr(t)
                return s or 200, ""
            return None, ""
        if lang == "csharp":
            m = _CS_RESP_RX.search(t)
            if m:
                name, args = m.group(1), m.group(2)
                if name == "StatusCode":
                    return status_from_expr(args) or 500, ""
                return _CS_STATUS.get(name), ""
            return None, ""
        if lang == "python":
            m = re.match(r"^\(?(.+?)\s*,\s*(?:status\.)?(?:HTTP_)?([1-5]\d{2})(?:_[A-Z_]+)?\s*\)?$", t.strip(), re.S)
            if m and not re.search(r"\b(?:Response|JSONResponse|JsonResponse)\s*\(", t):
                return int(m.group(2)), ""
            m = _PY_RESP_RX.search(t)
            if m:
                name = m.group(1)
                sm = re.search(r"status(?:_code)?\s*=\s*([^,)]+)", t)
                if sm:
                    return status_from_expr(sm.group(1)) or _PY_STATUS.get(name, 200), ""
                if name == "abort":
                    return status_from_expr(t) or 500, "HTTPException"
                # jsonify(x), 201  handled above; Response(..., 404) positional
                pm = re.search(r"\b(?:Response|make_response|HttpResponse|JsonResponse)\s*\([^,]*,\s*(?:status\s*=\s*)?([\w.]+)", t)
                if pm:
                    s = status_from_expr(pm.group(1))
                    if s:
                        return s, ""
                return _PY_STATUS.get(name, 200), ""
            return None, ""
        if lang == "go":
            if re.search(r"\b(?:echo\.NewHTTPError|fiber\.NewError|status\.Error)\(", t):
                return status_from_expr(t) or 500, _go_error_name(t)
            if self._is_response_expr(t, lang):
                return status_from_expr(t) or 200, ""
            return None, ""
        # javascript / typescript
        if self._is_response_expr(t, lang):
            s = status_from_expr(_status_args(t))
            if s:
                return s, ""
            if re.search(r"\.(redirect|Redirect)\(", t):
                return 302, ""
            if re.search(r"\.(sendStatus|status|code)\(\s*(\d{3})", t):
                return int(re.search(r"\.(?:sendStatus|status|code)\(\s*(\d{3})", t).group(1)), ""
            return 200, ""
        m = re.search(r"\bnew\s+(NextResponse|Response)\s*\((?:[^,]*,)?\s*\{[^}]*status\s*:\s*(\d{3})", t)
        if m:
            return int(m.group(2)), ""
        m = re.search(r"\b(?:NextResponse|Response)\.json\s*\((?:[^{]*\{[^}]*\}\s*,)?[^)]*status\s*:\s*(\d{3})", t)
        if m:
            return int(m.group(1)), ""
        if re.search(r"\b(?:NextResponse|Response)\.(json|redirect)\(", t):
            return 302 if ".redirect(" in t else 200, ""
        if re.search(r"\bc\.(json|text|html|body)\(\s*[^,]*,\s*(\d{3})", t):   # hono: c.json(x, 201)
            return int(re.search(r",\s*(\d{3})", t).group(1)), ""
        return None, ""

    def _is_response_expr(self, text: str, lang: str) -> bool:
        if lang in ("javascript", "typescript"):
            return bool(re.search(r"\b(res|response|reply|rep|ctx|c|resp|h)\s*\.\s*(status|sendStatus|json|send|end|render|redirect|sendFile|download|jsonp|code|view|type|body|text|html|response)\s*\(|\bctx\.(body|status)\s*=", text))
        if lang == "go":
            return bool(re.search(r"\b(w|rw|c|ctx|resp|writer)\s*\.\s*(WriteHeader|Write|JSON|IndentedJSON|PureJSON|SecureJSON|String|XML|YAML|Data|HTML|Redirect|Status|NoContent|AbortWithStatus|AbortWithStatusJSON|AbortWithError|Blob|File|Attachment|Stream|SendStatus|SendString|Send|Render|Error)\s*\(|\bhttp\.(Error|Redirect|ServeFile|ServeContent|NotFound)\(|\brender\.(JSON|Status|Render|PlainText|HTML|Data|NoContent)\(|json\.NewEncoder\(\s*w\s*\)\.Encode|fmt\.Fprint\w*\(\s*w\b|\bw\.Write\(|templ?\w*\.Execute\(\s*w\b", text))
        return False

    # ------------------------------------------------------------------ expressions
    def _expr(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx, in_branch: bool) -> List[Step]:
        lang = scope.fn.lang
        text = st.text or ""
        out: List[Step] = []
        if not text.strip():
            return out
        # koa / fastapi style status assignment
        m = re.match(r"^(?:ctx|response|res|reply)\.(?:status|status_code|statusCode)\s*=\s*(.+)$", text)
        if m:
            s = status_from_expr(m.group(1))
            if s:
                scope.pending_status = s
            return out
        # python assert -> validation
        if lang == "python" and text.startswith("assert "):
            step = Step(kind="validate", label="Check that " + labeler.humanize_condition(text[7:].split(",")[0]), detail=text[:140],
                        location=Location(scope.fn.file.path, st.line), code=self._code(text))
            out.append(step)
            ctx.steps_made += 1
            return out
        # response emitted from an expression statement (res.json, w.WriteHeader, ctx.body = ...)
        if self._is_response_expr(text, lang):
            status, throws = self._outcome_of(text, lang, scope)
            if status is None:
                status = scope.pending_status or ctx.default_status or 200
            if re.match(r"^ctx\.status\s*=", text):
                scope.pending_status = status
                return out
            out.extend(self._calls_in(st, scope, depth, path, ctx, skip_response=True))
            if lang == "go" and re.search(r"\bhttp\.Error\(|AbortWithStatus|AbortWithError", text) or (status and status >= 400):
                label = labeler.return_label(status, "", depth, lang)
            else:
                label = labeler.return_label(status, text, depth, lang)
            step = Step(kind="return", label=label, detail=text[:160], outcome={"status": status},
                        location=Location(scope.fn.file.path, st.line), code=self._code(text))
            out.append(step)
            ctx.steps_made += 1
            scope.pending_status = None
            return out
        out.extend(self._calls_in(st, scope, depth, path, ctx))
        # lambda bodies: attach to the last call step of this statement, else inline
        lambda_steps: List[Step] = []
        for lb in st.lambdas:
            lambda_steps.extend(self._convert(lb, scope, depth, path, ctx, in_branch))
        if text.startswith("with ") and re.search(r"(?:^|\.)([A-Z]\w*(?:Client|Session|Connection|Pool|Channel|Producer|Consumer|Cursor|Reader|Writer|Stream))\s*\(", text):
            # `with httpx.AsyncClient() as client:` is setup, not a call: show what happens inside
            out = [s for s in out if not (s.kind.startswith("io.") or s.kind == "external")]
            out.extend(lambda_steps)
            return out
        if lambda_steps:
            host = next((s for s in reversed(out) if s.kind in ("call", "external", "io.db", "io.http", "io.queue", "io.cache", "io.file", "transform")), None)
            if host is not None and not host.children:
                host.children = lambda_steps
                if host.kind != "call":
                    host.tags.append("callback")
            else:
                out.extend(lambda_steps)
        return out

    def _calls_in(self, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str], ctx: _Ctx,
                  skip_response: bool = False, skip_exceptions: bool = False) -> List[Step]:
        lang = scope.fn.lang
        text = st.text or ""
        sf = scope.fn.file
        if lang == "python":
            masked = lexer.mask(text, "python")
        else:
            masked = sf.masked[st.pos:st.pos + len(text)] if st.pos and sf.masked[st.pos:st.pos + len(text)].replace(" ", "") else lexer.mask(text, lang)
            if len(masked) != len(text):
                masked = lexer.mask(text, lang)
        found: List[Tuple[int, int, str, str, bool, bool]] = []   # (end, start, chain, args, chained, is_new)
        for m in _CALL_RX.finditer(masked):
            chain = re.sub(r"\s+", "", m.group(1)).replace("?.", ".").replace("::", ".").replace("->", ".")
            last = chain.split(".")[-1]
            if last in _KEYWORDS or chain in _KEYWORDS:
                continue
            if "." not in chain and last in _BARE_NOISE and last != "open":
                continue
            open_idx = m.end() - 1
            close = lexer.match_bracket(masked, open_idx)
            if close < 0:
                close = len(masked) - 1
            k = m.start() - 1
            while k >= 0 and masked[k] in " \t\n":
                k -= 1
            prev = masked[k] if k >= 0 else ""
            is_new = bool(re.search(r"\bnew\s*$", masked[max(0, m.start() - 6):m.start()]))
            chained = prev in (".", ")")   # receiver is the result of the preceding expression
            args = text[open_idx + 1:close]
            found.append((close, m.start(), chain, args, chained, is_new))
        found.sort(key=lambda f: (f[0], -f[1]))
        steps: List[Step] = []
        seen_spans: Set[Tuple[int, int]] = set()
        for close, start, chain, args, chained, is_new in found:
            if (start, close) in seen_spans:
                continue
            seen_spans.add((start, close))
            if skip_response and self._is_response_call(chain, lang):
                continue
            if is_new and (skip_exceptions or _EXC_CLASS_RX.match(chain)):
                continue
            if skip_exceptions and chain.split(".")[-1][:1].isupper():
                continue
            step = self._call_step(chain, args, chained, is_new, st, scope, depth, path, ctx, text)
            if step is None:
                # chain modifiers (orElseThrow etc.) attach to the previous significant step
                self._apply_modifier(chain, args, steps, scope)
                continue
            steps.append(step)
            ctx.steps_made += 1
        # python get_object_or_404 & friends
        if lang == "python" and re.search(r"\bget_(?:object|list)_or_404\(", text):
            for s in steps:
                if "or_404" in s.detail:
                    s.outcome["status"] = 404
                    s.tags.append("or-fails")
                    s.label += " (404 if missing)"
        return steps

    def _is_response_call(self, chain: str, lang: str) -> bool:
        parts = chain.split(".")
        if len(parts) < 2:
            if lang in ("java", "kotlin"):
                return False
            return parts[0] in ("jsonify", "make_response", "abort", "redirect", "render_template", "Ok", "NotFound", "BadRequest", "Created",
                                "NoContent", "Accepted", "Conflict", "Unauthorized", "Forbid", "StatusCode", "Problem", "Response",
                                "JSONResponse", "JsonResponse", "HttpResponse", "RedirectResponse", "CreatedAtAction", "CreatedAtRoute")
        root, meth = parts[0], parts[-1]
        if root in ("ResponseEntity", "Response", "ServerResponse", "Results", "TypedResults", "NextResponse", "render", "http") and lang != "python":
            return meth in ("ok", "status", "notFound", "badRequest", "noContent", "accepted", "created", "of", "body", "build", "headers",
                            "unprocessableEntity", "internalServerError", "json", "redirect", "Ok", "NotFound", "BadRequest", "Created", "NoContent",
                            "JSON", "Status", "Render", "PlainText", "Error", "Redirect", "NotFound", "ServeFile", "Data", "HTML", "contentType",
                            "cacheControl", "header", "location", "eTag", "lastModified", "bodyValue", "entity", "type", "Problem", "Json")
        if root in RESPONSE_OBJECTS or root in ("Response", "JSONResponse", "JsonResponse"):
            return meth in RESPONSE_METHODS
        if root == "json" and meth in ("NewEncoder",) or chain.endswith("NewEncoder(w).Encode") or (root == "fmt" and meth.startswith("Fprint")):
            return True
        return False

    def _apply_modifier(self, chain: str, args: str, steps: List[Step], scope: _Scope) -> None:
        meth = chain.split(".")[-1]
        if not steps:
            return
        host = steps[-1]
        if meth in ("orElseThrow", "getOrThrow", "switchIfEmpty", "onErrorMap", "orElseThrowMono"):
            exc = ""
            m = re.search(r"(?:new\s+|::new|error\(\s*new\s+)?\b([A-Z]\w*(?:Exception|Error|Fault)?)\s*(?:\(|::new)", args)
            if m:
                exc = m.group(1)
            if meth == "switchIfEmpty" and not re.search(r"error\(", args):
                return
            if not exc:
                exc = "NoSuchElementException" if meth == "orElseThrow" else "error"
            status = self._status_for_exception(exc, args)
            host.outcome["throws"] = exc
            if status:
                host.outcome["status"] = status
            host.tags.append("or-fails")
            host.label += f" — or fail: {labeler.throw_label(exc, status)[6:]}"
        elif meth in ("orElse", "orElseGet", "getOrElse", "getOrDefault", "defaultIfEmpty"):
            host.tags.append("has-default")
        elif meth in ("timeout", "retry", "retryWhen", "retryBackoff"):
            host.tags.append(meth)
        elif meth in ("onErrorResume", "onErrorReturn", "catch", "recover", "doOnError", "catchError"):
            host.tags.append("handles-error")

    # ------------------------------------------------------------------ call steps
    def _call_step(self, chain: str, args: str, chained: bool, is_new: bool, st: lexer.Stmt, scope: _Scope, depth: int, path: Set[str],
                   ctx: _Ctx, stmt_text: str) -> Optional[Step]:
        lang = scope.fn.lang
        sf = scope.fn.file
        parts = chain.split(".")
        meth = parts[-1]
        recv_parts = parts[:-1]
        if recv_parts and recv_parts[0] in ("this", "self") and len(recv_parts) > 1:
            recv_parts = recv_parts[1:]
        recv = ".".join(recv_parts)
        recv_root = recv_parts[0] if recv_parts else ""
        recv_last = recv_parts[-1] if recv_parts else ""
        loc = Location(sf.path, st.line)
        code = self._code(stmt_text)

        # user-config noise
        if any(rx.search(chain) for rx in self.noise_rx):
            return None
        if is_new:
            return None   # object construction is plumbing; the call that uses the object is the step
        args = _subst_consts(args, scope.consts)
        if meth in ("orElseThrow", "getOrThrow", "switchIfEmpty", "onErrorMap", "orElse", "orElseGet", "getOrElse", "getOrDefault",
                    "defaultIfEmpty", "timeout", "retry", "retryWhen", "onErrorResume", "onErrorReturn", "doOnError", "catchError", "recover") and (chained or recv):
            if meth in ("catch", "recover") and not chained:
                pass
            else:
                return None

        # feature flags
        if labeler.is_feature_flag(chain) or any(rx.search(chain) for rx in self.flag_rx):
            fm = re.search(r"['\"]([\w.\-:/]+)['\"]", args)
            name = fm.group(1) if fm else ""
            step = Step(kind="external", label=f"Check feature flag '{name}'" if name else "Check feature flag", detail=f"{chain}({args[:60]})",
                        target=chain, location=loc, code=code, tags=["feature-flag"] + ([f"flag:{name}"] if name else []))
            return step

        # async dispatch
        if ASYNC_RX.search(chain) and not recv_root in NOISE_RECEIVERS:
            return Step(kind="external", label="Run in the background: " + labeler.humanize(meth), detail=f"{chain}({args[:60]})", target=chain,
                        location=loc, code=code, tags=["async"])

        # logging / metrics
        if (recv_root and LOG_RX.match(recv_root)) or (not recv and meth in ("log", "print", "println", "printf", "debug", "info", "warn", "error", "trace")):
            if "log" in self.cfg.hide_kinds and not self.cfg.raw.get("show_logs"):
                return Step(kind="log", label="Log: " + labeler.humanize(meth), detail=f"{chain}({args[:60]})", target=chain, location=loc)
            return Step(kind="log", label="Log " + labeler.humanize(meth), detail=f"{chain}({args[:60]})", target=chain, location=loc)

        # resolve inside the repo first
        resolved, owner_name, receiver_type = self._resolve(recv_parts, meth, scope, chained)
        if resolved is not None:
            target = resolved.qualname if resolved.owner else f"{resolved.file.stem}.{resolved.name}"
            owner_cls = resolved.owner
            # repository / http-client interfaces
            if owner_cls in self.index.repositories:
                entity = self.index.repositories.get(owner_cls, "")
                return Step(kind="io.db", label=labeler.db_label(meth, entity or labeler.type_words(owner_cls).replace(" repository", ""), labeler.type_words(owner_cls), args),
                            detail=f"{target}({args[:80]})", target=target, location=loc, code=code)
            if owner_cls in self.index.http_clients:
                info = self.index.http_clients[owner_cls]
                return Step(kind="io.http", label=labeler.http_label(info.get("service", ""), info.get("methods", {}).get(meth, ""), meth, args),
                            detail=f"{target}({args[:80]})", target=target, location=loc, code=code)
            if _is_trivial_accessor(resolved, self._stmts):
                return None
            step = Step(kind="call", label=labeler.call_label(meth, owner_cls or resolved.file.stem, recv_last, True), detail=f"{target}({args[:80]})", target=target, location=loc, code=code)
            if not resolved.has_body:
                step.tags.append("interface-only")
                step.label = labeler.call_label(meth, owner_cls, recv_last, False)
                step.kind = "external"
                return step
            if id(resolved) in path or resolved is scope.fn:
                step.tags.append("recursion")
                return step
            if depth + 1 >= self.cfg.max_depth:
                step.tags.append("depth-limit")
                return step
            if ctx.steps_made < ctx.budget:
                child_owner = self.index.class_named(owner_cls, resolved.file.path) if owner_cls else None
                step.children = self._trace_fn(resolved, depth + 1, path | {id(resolved)}, ctx, child_owner)
                self._collapse_thin_wrapper(step, meth, args)
                if step.kind == "call" and not step.children and _PLUMBING_NAME_RX.match(meth):
                    return None   # factory / mapper / builder with nothing inside worth showing
            return step

        # not in repo: classify by pattern on the chain and on the receiver's declared type
        subject = chain if not receiver_type else f"{receiver_type}.{meth}"
        if receiver_type in self.index.http_clients:
            info = self.index.http_clients[receiver_type]
            return Step(kind="io.http", label=labeler.http_label(info.get("service", ""), info.get("methods", {}).get(meth, ""), meth, args),
                        detail=f"{subject}({args[:80]})", target=subject, location=loc, code=code)
        if receiver_type in self.index.repositories:
            return Step(kind="io.db", label=labeler.db_label(meth, self.index.repositories.get(receiver_type, ""), labeler.type_words(receiver_type), args),
                        detail=f"{subject}({args[:80]})", target=subject, location=loc, code=code)
        probe = " ".join(x for x in (chain, subject, stmt_text[:200]) if x)
        kind = self._io_kind(chain, receiver_type, meth)
        rwords = labeler.type_words(receiver_type) if receiver_type else labeler.humanize(recv_last or recv_root)
        if kind == "io.db":
            entity = ""
            if GENERIC_DB_RECEIVERS.match(recv_last or recv_root or "") or not (recv_last or recv_root):
                # session.add(order) / em.find(Order.class, id) / db.orders.create(...)
                am = re.match(r"^\s*(?:new\s+)?([A-Za-z_]\w*)", args or "")
                if am and am.group(1) not in ("null", "None", "nil", "true", "false", "this", "self", "req", "request", "ctx", "id", "ids"):
                    entity = re.sub(r"(Entity|Model|Record|Row|Dto|DTO)$", "", am.group(1))
                if not entity and recv_last and recv_last != recv_root and not GENERIC_DB_RECEIVERS.match(recv_last):
                    entity = recv_last
            elif recv_last and recv_last != recv_root and GENERIC_DB_RECEIVERS.match(recv_root or ""):
                entity = recv_last   # db.orders.create -> orders
            return Step(kind=kind, label=labeler.db_label(meth, entity, "" if entity else rwords, args, stmt_text), detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if kind == "io.http":
            if meth == "Do" and re.fullmatch(r"\s*(?:req|request|r|httpReq)\w*\s*", args or ""):
                return None   # the request was described where it was built (http.NewRequest)
            if meth in ("NewRequest", "NewRequestWithContext"):
                vm = re.search(r"http\.Method(\w+)|['\"](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"]", args or "")
                verb_for_label = (vm.group(1) or vm.group(2)).upper() if vm else ""
                meth = verb_for_label.lower() or meth
            generic = re.match(r"^(axios|fetch|got|http|https|requests|httpx|client|instance|ky|needle|superagent|request|urllib|aiohttp|resty|restTemplate|webClient|httpClient|okHttpClient|restClient|session|api)$", recv_root or "", re.I) \
                or re.match(r"^(RestTemplate|WebClient|HttpClient|OkHttpClient|Retrofit|RestClient|IHttpClientFactory|HttpClientFactory|Client|Session|AsyncClient|ClientSession|Http)$", receiver_type or "")
            service = rwords if (receiver_type or recv_root) and not generic else ""
            if not service and scope.fn.owner and re.search(r"(Client|Gateway|Adapter|Connector|Api|Proxy|Service|Facade)$", scope.fn.owner):
                service = re.sub(r"\b(client|gateway|adapter|connector|api|proxy|facade)$", "", labeler.type_words(scope.fn.owner)).strip() + " service"
            return Step(kind=kind, label=labeler.http_label(service, "", meth, args), detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if kind == "io.queue":
            return Step(kind=kind, label=labeler.queue_label(meth, rwords if not re.match(r"^(kafka|sqs|sns|producer|publisher|bus|queue|topic)$", recv_root or "", re.I) else "", args),
                        detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if kind == "io.cache":
            return Step(kind=kind, label=labeler.cache_label(meth, args), detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if kind == "io.file":
            return Step(kind=kind, label=labeler.sentence(("read from " if re.search(r"get|read|download|open|load|list", meth, re.I) else "write to ") + (rwords or "storage") + (": " + labeler.humanize(meth) if len(meth) > 3 else "")),
                        detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if is_new:
            return None
        if chain.endswith("NewDecoder") and re.search(r"\b(r|req|request|c)\.Body\b|\.Body\b", args):
            return Step(kind="transform", label="Parse request body", detail=f"{chain}({args[:60]})", target=chain, location=loc, code=code)
        if REQUEST_PARSE_RX.match(chain):
            if re.search(r"Bind|Validate|parse", meth, re.I) and lang == "go":
                return Step(kind="validate", label="Parse and validate request body", detail=f"{chain}({args[:60]})", target=chain, location=loc, code=code)
            if meth in ("get_json", "json", "body", "text", "formData", "ParseForm", "form", "data", "arrayBuffer", "MultipartForm", "FormFile", "PostForm", "parseBody"):
                return Step(kind="transform", label="Read request body", detail=f"{chain}({args[:60]})", target=chain, location=loc)
            return None
        if any(rx.search(chain) for rx in self.auth_rx) or AUTH_RX.search(chain) and not GETTER_RX.match(meth) or (AUTH_RX.search(recv_root or "") and receiver_type):
            return Step(kind="auth", label=labeler.sentence(labeler.humanize(meth) if not receiver_type else f"{labeler.humanize(meth)} ({rwords})"),
                        detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if any(rx.search(chain) for rx in self.validation_rx) or VALIDATE_RX.search(chain) and not chain.startswith(("Objects", "Optional")):
            what = labeler.humanize(meth)
            return Step(kind="validate", label=labeler.sentence(what if "valid" in what or "check" in what or "verify" in what or "ensure" in what or "require" in what or "assert" in what else "validate: " + what),
                        detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        if TRANSFORM_RX.search(chain) and not (recv_root in NOISE_RECEIVERS and meth in NOISE_METHODS):
            return Step(kind="transform", label=labeler.sentence(labeler.humanize(meth) if len(meth) > 3 and meth not in ("map",) else "transform: " + labeler.humanize(recv_last or meth)),
                        detail=f"{chain}({args[:80]})", target=subject, location=loc, code=code)
        # noise
        if recv_root in NOISE_RECEIVERS or recv_last in NOISE_RECEIVERS:
            return None
        if receiver_type in ("Request", "Context", "ResponseWriter", "HttpServletRequest", "HttpServletResponse", "HttpRequest", "HttpResponse",
                             "ServerHttpRequest", "ServerWebExchange", "HttpContext", "Response", "Writer", "Reader", "Body", "Headers", "Header", "URL") \
                or receiver_type in VIEW_PLUMBING_TYPES:
            return None
        if GETTER_RX.match(meth) and receiver_type and not COLLABORATOR_TYPE_RX.search(receiver_type):
            return None   # accessor on a domain object / DTO we could not expand: plumbing
        if meth in NOISE_METHODS:
            return None
        if receiver_type in COLLECTION_TYPES:
            return None
        if GETTER_RX.match(meth) and (not args.strip() or meth.startswith("set")) and not receiver_type:
            return None
        if chained and meth in NOISE_METHODS:
            return None
        # keep: known collaborator (declared type) or business-verb call
        if receiver_type and receiver_type not in COLLECTION_TYPES:
            if GETTER_RX.match(meth) and not args.strip():
                return None
            return Step(kind="external", label=labeler.call_label(meth, receiver_type, recv_last, False), detail=f"{subject}({args[:80]})", target=subject,
                        location=loc, code=code, tags=["unresolved"])
        if BUSINESS_VERB_RX.match(meth) and not chained:
            label = labeler.call_label(meth, "", recv_last or recv_root, bool(recv_last) is False)
            if recv_last:
                label = labeler.sentence(f"{labeler.humanize(meth)} ({labeler.humanize(recv_last)})")
            return Step(kind="external", label=label, detail=f"{chain}({args[:80]})", target=chain, location=loc, code=code, tags=["unresolved"])
        if not recv and not chained and len(meth) > 3 and lang in ("python", "go") and BUSINESS_VERB_RX.match(labeler.split_words(meth)[0] if labeler.split_words(meth) else meth):
            return Step(kind="external", label=labeler.call_label(meth), detail=f"{chain}({args[:80]})", target=chain, location=loc, code=code, tags=["unresolved"])
        return None

    def _collapse_thin_wrapper(self, step: Step, meth: str, args: str) -> None:
        """`eventPublisher.publish(x)` whose body is just `kafka.send(topic, x)`
        reads better as the queue step itself, labelled with the caller's
        literals (topic name, event type)."""
        meaningful = [c for c in step.children if c.kind != "log"]
        if len(meaningful) != 1 or not meaningful[0].kind.startswith("io."):
            return
        child = meaningful[0]
        step.tags.append("wrapper")
        step.kind = child.kind
        merged_args = args + " " + (child.detail.split("(", 1)[1] if "(" in child.detail else "")
        if child.kind == "io.queue":
            broker = ""
            for key, name in labeler._BROKERS:
                if key in child.label.lower():
                    broker = name
                    break
            step.label = labeler.queue_label(meth, broker, merged_args)
        elif child.kind == "io.http":
            step.label = child.label
        else:
            step.label = child.label
        if child.outcome and not step.outcome:
            step.outcome = dict(child.outcome)
        if child.kind == step.kind and not child.children and not child.branches:
            step.detail = f"{step.detail} → {child.detail}"
            step.children = []

    def _io_kind(self, chain: str, receiver_type: str, meth: str) -> str:
        probe = chain + (" " + receiver_type + "." + meth if receiver_type else "")
        if re.search(r"(Sqs|SQS|Sns|SNS|Kafka|Rabbit|Jms|Kinesis|PubSub|EventBridge|ServiceBus|Nats|Pulsar|Queue|Topic|Producer|Publisher|MessageBus|EventBus)", receiver_type or ""):
            return "io.queue"
        if re.search(r"(Redis|Cache|Memcache|Jedis|Lettuce)", receiver_type or ""):
            return "io.cache"
        # explicit type-name based first (most reliable)
        for kind in ("io.queue", "io.cache", "io.file", "io.http", "io.db"):
            for rx in self.io_rx.get(kind, []):
                if rx.search(probe):
                    # avoid cache false positives like "cached order" fields on business services
                    if kind == "io.cache" and receiver_type and not re.search(r"[Cc]ache|[Rr]edis|Memcache|Jedis|Lettuce", receiver_type + chain):
                        continue
                    if kind == "io.db" and re.search(r"^(this|self)?\.?(req|request|ctx|c)\.", chain):
                        continue
                    return kind
        return ""

    def _resolve(self, recv_parts: List[str], meth: str, scope: _Scope, chained: bool) -> Tuple[Optional[FunctionDef], str, str]:
        """Returns (function or None, owner class name, declared receiver type)."""
        idx = self.index
        fn = scope.fn
        lang = fn.lang
        file_path = fn.file.path
        recv = recv_parts[-1] if recv_parts else ""
        receiver_type = ""
        if chained and not recv_parts:
            return None, "", ""
        if not recv_parts:
            # bare call: same class, same file, imports, unique
            if fn.owner:
                f = idx.method_of(fn.owner, meth, file_path)
                if f:
                    return f, f.owner, fn.owner
            f = idx.function_in_file(file_path, meth)
            if f:
                return f, f.owner, ""
            imp = idx.resolve_import(file_path, meth)
            if imp and imp[0]:
                f = idx.function_in_file(imp[0], meth if imp[1] in ("default", "*") else imp[1])
                if f:
                    return f, f.owner, ""
            if lang == "go":
                for f in idx.go_packages.get(fn.package, []):
                    if f.name == meth and f.has_body:
                        return f, "", ""
            f = idx.unique_function(meth, file_path)
            if f and lang not in ("python",):
                return f, f.owner, ""
            if f and lang == "python" and f.file.path == file_path:
                return f, f.owner, ""
            return None, "", ""
        # receiver chain
        root = recv_parts[0]
        if len(recv_parts) == 1:
            receiver_type = scope.types.get(root, "")
            if root in ("this", "self") and fn.owner:
                f = idx.method_of(fn.owner, meth, file_path)
                return (f, f.owner if f else "", fn.owner)
            if receiver_type:
                f = idx.method_of(receiver_type, meth, file_path)
                if f:
                    return f, f.owner, receiver_type
            # imported module / namespace
            imp = idx.resolve_import(file_path, root)
            if imp and imp[0]:
                if imp[1] in ("*", "default"):
                    f = idx.function_in_file(imp[0], meth)
                    if f is None:
                        exports = idx.exports.get(imp[0], {})
                        local = exports.get(meth)
                        if local:
                            f = idx.function_in_file(imp[0], local)
                        if f is None and exports.get("default"):
                            f = idx.method_of(exports["default"], meth, imp[0])
                        if f is None:
                            hint = idx.module_vars.get(imp[0], {}).get(exports.get("default", ""))
                            if hint:
                                f = idx.method_of(hint, meth, imp[0])
                    if f:
                        return f, f.owner, receiver_type
                else:
                    f = idx.method_of(imp[1], meth, imp[0])
                    if f:
                        return f, f.owner, imp[1]
            # go package / static class
            if lang == "go":
                for f in idx.go_packages.get(root, []):
                    if f.name == meth and f.has_body:
                        return f, "", ""
            if root and root[0].isupper():
                f = idx.method_of(root, meth, file_path)
                if f:
                    return f, f.owner, root
                receiver_type = receiver_type or root
            # unknown receiver: unique method name across repo (only when unambiguous)
            if not receiver_type:
                f = idx.unique_function(meth, file_path)
                if f and f.owner and lang not in ("python", "go"):
                    return f, f.owner, ""
            return None, "", receiver_type
        # nested receivers: a.b.method  -> type of b via a's class fields
        t = scope.types.get(root, "")
        cur = t
        for p in recv_parts[1:]:
            cls = idx.class_named(cur) if cur else None
            if cls and p in cls.fields:
                cur = cls.fields[p]
            else:
                cur = ""
                break
        receiver_type = cur or scope.types.get(recv, "")
        if receiver_type:
            f = idx.method_of(receiver_type, meth, file_path)
            if f:
                return f, f.owner, receiver_type
        if recv and recv[0].isupper():
            f = idx.method_of(recv, meth, file_path)
            if f:
                return f, f.owner, recv
        return None, "", receiver_type

    # ------------------------------------------------------------------ helpers
    def _code(self, text: str) -> str:
        if not self.cfg.snippets or not text:
            return ""
        t = re.sub(r"\s+", " ", text.strip())
        if len(t) > 180:
            t = t[:177] + "…"
        return redact(t) if self.cfg.redact else t


# ---------------------------------------------------------------------------- helpers

_PLUMBING_NAME_RX = re.compile(r"^(from|to|of|build|make|new|parse|format|convert|map|as|is|has|get|set|with|copy|clone|init|__init__|wrap|unwrap|create)(?:[A-Z_]|$)")
_ACCESSOR_RX = re.compile(r"^(get|set|is|has|with|to|as)[A-Z]\w*$|^(get_|set_|is_|has_)\w+$")


def _is_trivial_accessor(fn: FunctionDef, stmts_of) -> bool:
    """True for getters/setters/tiny DTO helpers whose body has nothing worth showing."""
    if not _ACCESSOR_RX.match(fn.name):
        return False
    try:
        stmts = stmts_of(fn)
    except Exception:
        return True
    if len(stmts) > 2:
        return False
    for st in stmts:
        if st.kind not in ("return", "expr"):
            return False
        if re.search(r"\b(?:[a-z_]\w*)\.(?!(get|set|is|has|to|as)[A-Z])[a-z_]\w*\s*\(", st.text or ""):
            # calls something that is not itself an accessor -> keep
            return False
    return True


def _subst_consts(args: str, consts: Dict[str, str]) -> str:
    """Inline string constants (topic names, cache keys) so labels can show them.
    URL-ish constants are left alone: the variable name reads better than a host."""
    if not consts or not args:
        return args

    def repl(m):
        v = consts.get(m.group(1))
        if v is None or "://" in v or v.startswith("/") and len(v) > 40:
            return m.group(0)
        return f'"{v}"'
    return re.sub(r"(?<![\w\"'{])([A-Z][A-Z0-9_]{2,}|[A-Za-z_]\w*)(?![\w\"'}])", repl, args)


def _declared_return_type(fn: FunctionDef) -> str:
    sig = fn.sig or ""
    lang = fn.lang
    if lang in ("kotlin", "typescript"):
        m = re.search(r"\)\s*:\s*([\w.<>\[\]|?, ]+?)\s*(?:=>)?\s*$", re.sub(r"\s+", " ", sig))
        return m.group(1).strip() if m else ""
    if lang in ("java", "csharp"):
        s = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", sig)
        s = re.sub(r"\[[^\]]*\]", " ", s) if lang == "csharp" else s
        s = re.sub(r"\s+", " ", s).strip()
        m = re.search(r"([\w.<>\[\]?, ]+?)\s+" + re.escape(fn.name) + r"\s*\(", s)
        if not m:
            return ""
        t = m.group(1).strip()
        t = re.sub(r"^(?:(?:public|private|protected|static|final|synchronized|abstract|default|override|async|virtual|internal)\s+)+", "", t)
        return t
    if lang == "go":
        m = re.search(r"\)\s*(\(?[\w.*\[\], ]+\)?)\s*$", re.sub(r"\s+", " ", sig))
        return m.group(1).strip("() ") if m else ""
    if lang == "python":
        node = fn.py_node
        ret = getattr(node, "returns", None)
        if ret is not None:
            try:
                import ast
                return ast.unparse(ret)
            except Exception:
                return ""
    return ""


def _iter(steps: List[Step]):
    for s in steps:
        yield s
        yield from _iter(s.children)
        for b in s.branches:
            yield from _iter(b.steps)


def _merge_header_writes(steps: List[Step]) -> List[Step]:
    """Go/net-http: `w.WriteHeader(201)` followed by `json.NewEncoder(w).Encode(x)` is one response."""
    out: List[Step] = []
    for s in steps:
        if out and s.kind == "return" and out[-1].kind == "return" and re.search(r"WriteHeader|\.status\(|\.code\(|\.Status\(", out[-1].detail) \
                and not re.search(r"\.(json|send|end|JSON|Encode|Write|body)\b", out[-1].detail):
            prev = out[-1]
            status = prev.outcome.get("status")
            merged = Step(kind="return", label=s.label, detail=prev.detail + "; " + s.detail, outcome={"status": status} if status else dict(s.outcome),
                          location=prev.location, code=(prev.code + "; " + s.code).strip("; "))
            if status:
                body = labeler._return_body_words(s.detail)
                merged.label = labeler.return_label(status, s.detail, 0, "go") if body else labeler.return_label(status, "", 0, "go")
            out[-1] = merged
            continue
        out.append(s)
    return out


def _arm_exits(steps: List[Step]) -> bool:
    for s in steps:
        if s.kind in ("return", "throw"):
            return True
        if s.kind == "branch" and s.branches and all(b.exits for b in s.branches) and any(b.label == "Otherwise" for b in s.branches):
            return True
    return False


def _first_string(text: str) -> str:
    m = _STRING_RX.search(text or "")
    return m.group(1) if m else ""


def _status_args(text: str) -> str:
    """The arguments of status-carrying response methods in a JS/Go expression."""
    parts = []
    for m in re.finditer(r"\.(status|sendStatus|code|WriteHeader|Status|AbortWithStatus|AbortWithStatusJSON|JSON|IndentedJSON|String|Data|HTML|NoContent|Redirect|SendStatus|Error|XML)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", text):
        parts.append(m.group(2))
    m = re.search(r"\bhttp\.Error\([^,]+,[^,]+,\s*([^)]+)\)", text)
    if m:
        parts.append(m.group(1))
    return " ".join(parts)


def _exception_name(text: str, lang: str) -> str:
    t = text.strip()
    m = re.search(r"\bnew\s+([A-Z][\w.]*)", t)
    if m:
        return m.group(1).split(".")[-1]
    m = re.match(r"^([A-Z][\w.]*)\s*\(", t)
    if m:
        return m.group(1).split(".")[-1]
    m = re.search(r"\b(?:createError|createHttpError|httpError|Boom\.\w+|boom\.\w+)\s*\(", t)
    if m:
        return "HttpError"
    m = re.match(r"^([A-Za-z_][\w.]*)\s*$", t)
    if m:
        return m.group(1).split(".")[-1]
    m = _EXC_CLASS_RX.search(t)
    return m.group(1) if m else (t.split("(")[0].split(".")[-1][:40] if t else "")


def _go_error_name(text: str) -> str:
    m = re.search(r"\b(Err[A-Z]\w*|echo\.NewHTTPError|fiber\.NewError|status\.Errorf?|errors\.New|fmt\.Errorf|err)\b", text)
    if not m:
        return "error"
    name = m.group(1)
    if name in ("errors.New", "fmt.Errorf", "status.Error", "status.Errorf", "echo.NewHTTPError", "fiber.NewError"):
        s = _first_string(text)
        s = re.sub(r"\s*[:\-]?\s*%[wvsdq]\b", "", s).strip(" :")
        return s if s else "error"
    if name == "err":
        return "error"
    return name
