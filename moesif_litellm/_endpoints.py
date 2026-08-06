DEFAULT_BASE_URL = "https://api.moesif.net"

EVENTS_BATCH = "/v1/events/batch"
RULES = "/v1/rules"
CONFIG = "/v1/config"
CHAT_COMPLETIONS = "/v1/chat/completions"

CALL_TYPE_PATH_MAP = {
    "completion": "/v1/chat/completions",
    "acompletion": "/v1/chat/completions",
    "embedding": "/v1/embeddings",
    "aembedding": "/v1/embeddings",
    "text_completion": "/v1/completions",
    "atext_completion": "/v1/completions",
    "image_generation": "/v1/images/generations",
    "aimage_generation": "/v1/images/generations",
    "transcription": "/v1/audio/transcriptions",
    "atranscription": "/v1/audio/transcriptions",
    "speech": "/v1/audio/speech",
    "aspeech": "/v1/audio/speech",
}