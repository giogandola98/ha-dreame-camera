"""Constants for Dreame Camera."""

DOMAIN = "dreame_camera"
DREAME_VACUUM_DOMAIN = "dreame_vacuum"

CONF_VACUUM_ENTRY = "vacuum_entry"
CONF_PIN = "pin"
CONF_BRIDGE_HOST = "bridge_host"
CONF_BRIDGE_PORT = "bridge_port"
CONF_BRIDGE_TOKEN = "bridge_token"
CONF_MAX_MINUTES = "max_minutes"
CONF_AUDIO = "audio"
CONF_AI_URL = "ai_url"
CONF_AI_MODEL = "ai_model"
CONF_AI_CONFIRM_MODEL = "ai_confirm_model"
DEFAULT_AI_URL = "http://local-ollama-locale:11434"
DEFAULT_AI_MODEL = "hf.co/ggml-org/SmolVLM2-2.2B-Instruct-GGUF:latest"
DEFAULT_AI_CONFIRM_MODEL = "qwen2.5vl:3b"

DEFAULT_BRIDGE_HOST = "local-dreame-camera-bridge"
DEFAULT_BRIDGE_PORT = 8099
DEFAULT_RTSP_PORT = 8554
DEFAULT_MAX_MINUTES = 5

# MIoT camera service of Dreame vacuums (from the Dreame app's Monitor model)
SIID_CAMERA = 10001
AIID_CAMERA_OPERATE = 1
AIID_VOICE_OPERATE = 2
AIID_ACCESS_CODE = 4
PIID_MONITOR = 1
PIID_KEEP_ALIVE = 6
PIID_VOICE_OPERATE = 2
PIID_CODE_OPEN = 1100
PIID_CODE_VERIFY = 1102
SIID_DEVICE_KEEP_ALIVE = 14
PIID_DEVICE_KEEP_ALIVE = 4

KEEP_ALIVE_SECONDS = 20
