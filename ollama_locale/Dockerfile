# Immagine ufficiale Ollama, versione fissata.
FROM ollama/ollama:0.40.11
ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/data/models \
    OLLAMA_KEEP_ALIVE=10m \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1
ENTRYPOINT ["/bin/ollama"]
CMD ["serve"]
LABEL io.hass.type="addon" io.hass.arch="amd64"
