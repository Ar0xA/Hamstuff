"""
Ugly but works, to allow you to use multiple clients to talk to the same rig. For use with say, different FT8 clients through Gridtracker2's Call Roster

Setup and use:
- start hamlib's rigctld on 4535
- start the rigproxy
- start the clients, they connect to the proxy on 4532 (rigctl's default port)

Note: 
- CAT changes work from any client
- When 1 client is in Tx mode, the others keep going but obviously no data is send to them. They do not crash because the proxy tells them the rig is in TX mode.

"""

import asyncio
import logging

# --- CONFIGURATION ---
PROXY_PORT = 4532        # The port your WSJT-X instances point to (Hamlib Rigctld)
REAL_RIGCTLD_ADDR = ("127.0.0.1", 4535)  # The actual rigctld port talking to the radio
POLL_INTERVAL = 1.0      # How often the proxy updates its cache from the real radio
# ---------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class RigState:
    def __init__(self):
        self.cached_freq = "14074000\n"  # Default fallback frequency
        self.cached_mode = "USB\n1500\n" # Default fallback mode
        self.cached_ptt = "0\n"          # Default: Rig is in RX (0)
        self.is_transmitting = False
        self.real_reader = None
        self.real_writer = None
        self.lock = asyncio.Lock()       # Prevents collisions

async def update_cache_loop(state):
    """Background loop that polls the real radio only when not transmitting."""
    while True:
        try:
            if not state.real_writer:
                state.real_reader, state.real_writer = await asyncio.open_connection(*REAL_RIGCTLD_ADDR)
                logging.info("Connected to physical rigctld.")

            # Only poll physical rig if the proxy hasn't flagged an active TX session
            if not state.is_transmitting and state.real_writer:
                async with state.lock:
                    # 1. Query Frequency
                    state.real_writer.write(b"f\n")
                    await state.real_writer.drain()
                    freq = await state.real_reader.readline()
                    if freq:
                        state.cached_freq = freq.decode()

                    # 2. Query Mode
                    state.real_writer.write(b"m\n")
                    await state.real_writer.drain()
                    mode = await state.real_reader.readline()
                    passband = await state.real_reader.readline()
                    if mode and passband:
                        state.cached_mode = mode.decode() + passband.decode()

                    # 3. Query Real PTT State (Just to keep sync if toggled manually on rig)
                    state.real_writer.write(b"t\n")
                    await state.real_writer.drain()
                    ptt = await state.real_reader.readline()
                    if ptt:
                        state.cached_ptt = ptt.decode()

        except Exception as e:
            logging.error(f"Error polling real rigctld: {e}")
            state.real_writer = None  # Force reconnection next loop
            
        await asyncio.sleep(POLL_INTERVAL)

async def forward_and_gather_response(state, data_to_send, client_writer):
    """Forwards commands to the physical rig and safely gathers multi-line responses."""
    if not state.real_writer:
        client_writer.write(b"RPRT -1\n")
        await client_writer.drain()
        return

    async with state.lock:
        state.real_writer.write(data_to_send)
        await state.real_writer.drain()

        first_line = await state.real_reader.readline()
        client_writer.write(first_line)
        await client_writer.drain()

        while True:
            try:
                next_line = await asyncio.wait_for(state.real_reader.readline(), timeout=0.05)
                if not next_line:
                    break
                client_writer.write(next_line)
                await client_writer.drain()
            except asyncio.TimeoutError:
                break

async def handle_client(reader, writer, state):
    """Handles incoming connections from WSJT-X/JTDX clients."""
    client_addr = writer.get_extra_info('peername')
    logging.info(f"New Rigctld client connected from: {client_addr}")
    
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            
            cmd = data.decode().strip()
            
            # 1. Handle Read Commands (Served instantly from Cache!)
            if cmd == "f":
                writer.write(state.cached_freq.encode())
                await writer.drain()
            elif cmd == "m":
                writer.write(state.cached_mode.encode())
                await writer.drain()
            elif cmd == "t":
                # Serve the cached PTT state instantly to all clients
                writer.write(state.cached_ptt.encode())
                await writer.drain()
                
            # 2. Handle PTT write / Configuration Commands
            else:
                # Intercept direct PTT write commands (e.g. 'T 1' or 'T 0')
                if cmd.startswith("T "):
                    tx_status = cmd.split()[1] # "1" for TX, "0" for RX
                    state.is_transmitting = (tx_status == "1")
                    # Update our local PTT cache so OTHER clients see it instantly when they poll 't'
                    state.cached_ptt = f"{tx_status}\n"
                    logging.info(f"PTT state updated by {client_addr} -> TX: {state.is_transmitting}")

                # Dynamically forward command to real rig and reply to sender
                await forward_and_gather_response(state, data, writer)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.debug(f"Error handling client {client_addr}: {e}")
    finally:
        logging.info(f"Rigctld client disconnected: {client_addr}")
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def main():
    state = RigState()
    asyncio.create_task(update_cache_loop(state))
    
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, state), 
        '127.0.0.1', 
        PROXY_PORT
    )
    
    logging.info(f"Rigctld Caching Proxy listening on 127.0.0.1:{PROXY_PORT}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down proxy.")
