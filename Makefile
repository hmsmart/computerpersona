PREFIX ?= /

BIN_DIR := $(PREFIX)usr/local/bin
ETC_DIR := $(PREFIX)etc/compusona
SYSTEMD_DIR := $(PREFIX)etc/systemd/system

.PHONY: install
install:
	install -d $(BIN_DIR) $(ETC_DIR) $(SYSTEMD_DIR)
	install -m 0755 compusona.py $(BIN_DIR)/compusona.py
	install -m 0600 env.example $(ETC_DIR)/env
	install -m 0644 config.toml.example $(ETC_DIR)/config.toml
	install -m 0644 persona.md.example $(ETC_DIR)/persona.md
	install -m 0644 compusona-shutdown.service.example $(SYSTEMD_DIR)/compusona-shutdown.service
	install -m 0644 compusona-boot.service.example $(SYSTEMD_DIR)/compusona-boot.service
	@if command -v systemctl >/dev/null 2>&1; then \
		systemctl daemon-reload; \
		echo "Run: systemctl enable compusona-shutdown.service compusona-boot.service"; \
	else \
		echo "systemctl not found; skipped daemon-reload"; \
	fi
