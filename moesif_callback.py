from moesif_litellm import MoesifLogger

# Module-level instance — reads MOESIF_APPLICATION_ID from environment.
# LiteLLM proxy loads this via litellm_settings.callbacks in config.yaml.
moesif_logger = MoesifLogger(debug=True)