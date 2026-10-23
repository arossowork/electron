# Firmware core for home-thermostat-3 (docs/hwsw-codesign.md).
#   make test    regenerate board.h from design.yaml + interface.yaml (hwflow fw), build the host tests with gcc, run
#   make board   only regenerate ../build/fw/board.h
# The ESP-IDF port (port/esp-idf) uses the same core/ and board.h; it is not built here.

FW      := $(CURDIR)
PROJ    := $(abspath $(FW)/..)
ROOT    := $(abspath $(PROJ)/../..)
BUILD   := $(PROJ)/build/fw
BOARD_H := $(BUILD)/board.h
CC      ?= gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -O1 -I$(FW)/include -I$(FW)/core -I$(BUILD) -I$(FW)/test
CORE    := $(wildcard $(FW)/core/*.c)
TESTS   := $(FW)/test/sim_hal.c $(FW)/test/test_main.c

.PHONY: test board clean
test: $(BUILD)/fw_tests
	$(BUILD)/fw_tests

board: $(BOARD_H)

$(BOARD_H): $(PROJ)/design.yaml $(PROJ)/interface.yaml
	cd $(ROOT) && uv run hwflow fw $(PROJ)

$(BUILD)/fw_tests: $(CORE) $(TESTS) $(wildcard $(FW)/core/*.h $(FW)/include/*.h $(FW)/test/*.h) $(BOARD_H)
	$(CC) $(CFLAGS) -o $@ $(CORE) $(TESTS) -lm

clean:
	rm -f $(BUILD)/fw_tests
