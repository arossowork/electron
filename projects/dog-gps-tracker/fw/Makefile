FW := $(CURDIR)
PROJ := $(abspath $(FW)/..)
ROOT := $(abspath $(FW)/../../..)
FWK := $(ROOT)/fw/framework
GEN := $(PROJ)/build/fw
CFLAGS := -DFWK_SIM_TRACE=65536 -std=c11 -Wall -Wextra -Werror -Wno-unused-const-variable -I$(FWK)/include -I$(FWK)/test -I$(GEN) -I$(FW) -I$(FW)/lib -I$(FW)/modules
SRC := $(FWK)/src/fwk.c $(FWK)/test/fwk_sim.c $(wildcard $(FW)/lib/*.c) $(wildcard $(FW)/modules/*.c) $(FW)/app.c $(FW)/test/sim_board.c $(FW)/test/test_app.c
.PHONY: test
test: $(GEN)/board.h
	$(CC) $(CFLAGS) -o $(GEN)/app_tests $(SRC) -lm && $(GEN)/app_tests
$(GEN)/board.h: $(PROJ)/design.yaml $(PROJ)/interface.yaml
	cd $(ROOT) && uv run hwflow fw $(PROJ)
