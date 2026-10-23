# Host build of the thermostat firmware: framework + lib + modules + host tests. `make test` regenerates the
# contract headers first (hwflow fw), so a design.yaml or interface.yaml change reaches the code.
ROOT  := ../../..
FWK   := $(ROOT)/fw/framework
GEN   := ../build/fw
CC    ?= gcc
CFLAGS := -std=c11 -Wall -Wextra -Werror -Wno-unused-const-variable -O1 \
          -I$(FWK)/include -I$(FWK)/test -I$(GEN) -I. -Ilib -Imodules -Itest
SRC   := $(FWK)/src/fwk.c $(FWK)/test/fwk_sim.c lib/thermo_lib.c $(wildcard modules/*.c) app.c \
         test/sim_board.c test/test_app.c

.PHONY: gen test clean
gen:
	cd $(ROOT) && uv run hwflow fw projects/home-thermostat-3

test: gen
	$(CC) $(CFLAGS) -o $(GEN)/fw_tests $(SRC)
	$(GEN)/fw_tests
	$(MAKE) -C $(FWK) test

clean:
	rm -f $(GEN)/fw_tests
