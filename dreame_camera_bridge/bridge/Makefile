VENDOR ?= vendor
CFLAGS ?= -O2 -Wall -Wextra -Wno-unused-parameter
# The Tencent static libs are GCC 9 "slim LTO" objects: build with GCC 9.x (Ubuntu 20.04),
# e.g. via ./in_buildroot.sh make. Newer GCCs cannot read that bytecode.
LDFLAGS += -L$(VENDOR)/lib -static-libstdc++ -static-libgcc
LIBS = -Wl,--start-group -lapp_interface -lenet-core -lcurl -lz \
       -levent_extra -levent_core -levent_mbedtls -levent_pthreads \
       -ltinyxml2 -lminizip -lmbedtls -lmbedcrypto -lmbedx509 -Wl,--end-group \
       -lpthread -lstdc++ -lm -ldl

xp2p_bridge: xp2p_bridge.c $(VENDOR)/include/appWrapper.h
	$(CC) $(CFLAGS) -I$(VENDOR)/include -o $@ $< $(LDFLAGS) $(LIBS)

$(VENDOR)/include/appWrapper.h:
	sh fetch_sdk.sh $(VENDOR)

clean:
	rm -f xp2p_bridge

.PHONY: clean
