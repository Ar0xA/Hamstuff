import asyncio
import threading
import logging
import json
import os
import customtkinter as ctk

# Configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- UI Theme Configuration ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "rigproxy_config.json"

class ProxyState:
    """Shared state between the Asyncio backend and the GUI frontend."""
    def __init__(self):
        self.cached_freq = "14074000\n"
        self.cached_mode = "USB\n1500\n"
        self.cached_ptt = "0\n"
        self.is_transmitting = False
        
        # Connection status for UI
        self.backend_connected = False
        self.connected_clients = set()
        
        # Asyncio internals
        self.real_reader = None
        self.real_writer = None
        self.lock = asyncio.Lock()
        self.is_running = False

class AsyncProxyServer:
    """The core asynchronous proxy logic."""
    def __init__(self, state, proxy_host, proxy_port, real_host, real_port):
        self.state = state
        self.proxy_host = proxy_host
        self.proxy_port = int(proxy_port)
        self.real_host = real_host
        self.real_port = int(real_port)
        self.server = None
        self.poll_interval = 1.0

    async def update_cache_loop(self):
        while self.state.is_running:
            try:
                if not self.state.real_writer:
                    self.state.real_reader, self.state.real_writer = await asyncio.open_connection(self.real_host, self.real_port)
                    self.state.backend_connected = True
                    logging.info("Connected to physical rigctld.")

                if not self.state.is_transmitting and self.state.real_writer:
                    async with self.state.lock:
                        self.state.real_writer.write(b"f\n")
                        await self.state.real_writer.drain()
                        freq = await self.state.real_reader.readline()
                        if freq: self.state.cached_freq = freq.decode()

                        self.state.real_writer.write(b"m\n")
                        await self.state.real_writer.drain()
                        mode = await self.state.real_reader.readline()
                        passband = await self.state.real_reader.readline()
                        if mode and passband: self.state.cached_mode = mode.decode() + passband.decode()

                        self.state.real_writer.write(b"t\n")
                        await self.state.real_writer.drain()
                        ptt = await self.state.real_reader.readline()
                        if ptt: self.state.cached_ptt = ptt.decode()

            except Exception:
                self.state.backend_connected = False
                self.state.real_writer = None
                
            await asyncio.sleep(self.poll_interval)

    async def forward_and_gather_response(self, data_to_send, client_writer):
        if not self.state.real_writer:
            try:
                self.state.real_reader, self.state.real_writer = await asyncio.open_connection(self.real_host, self.real_port)
                self.state.backend_connected = True
            except Exception:
                client_writer.write(b"RPRT -1\n")
                await client_writer.drain()
                return

        async with self.state.lock:
            try:
                self.state.real_writer.write(data_to_send)
                await self.state.real_writer.drain()

                first_line = await self.state.real_reader.readline()
                if not first_line: raise ConnectionError()
                    
                client_writer.write(first_line)
                await client_writer.drain()

                while True:
                    try:
                        next_line = await asyncio.wait_for(self.state.real_reader.readline(), timeout=0.05)
                        if not next_line: break
                        client_writer.write(next_line)
                        await client_writer.drain()
                    except asyncio.TimeoutError:
                        break
            except Exception:
                self.state.backend_connected = False
                self.state.real_writer = None
                client_writer.write(b"RPRT -1\n")
                await client_writer.drain()

    async def handle_client(self, reader, writer):
        client_addr = f"{writer.get_extra_info('peername')[0]}:{writer.get_extra_info('peername')[1]}"
        self.state.connected_clients.add(client_addr)
        
        try:
            while self.state.is_running:
                data = await reader.readline()
                if not data: break
                
                cmd = data.decode().strip()
                if cmd == "f":
                    writer.write(self.state.cached_freq.encode())
                    await writer.drain()
                elif cmd == "m":
                    writer.write(self.state.cached_mode.encode())
                    await writer.drain()
                elif cmd == "t":
                    writer.write(self.state.cached_ptt.encode())
                    await writer.drain()
                else:
                    if cmd.startswith("T "):
                        tx_status = cmd.split()[1]
                        self.state.is_transmitting = (tx_status == "1")
                        self.state.cached_ptt = f"{tx_status}\n"
                    await self.forward_and_gather_response(data, writer)
        except Exception:
            pass
        finally:
            self.state.connected_clients.discard(client_addr)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception: pass

    async def start(self):
        self.state.is_running = True
        asyncio.create_task(self.update_cache_loop())
        self.server = await asyncio.start_server(self.handle_client, self.proxy_host, self.proxy_port)
        async with self.server:
            while self.state.is_running:
                await asyncio.sleep(0.5)
        self.server.close()
        await self.server.wait_closed()

class ProxyApp(ctk.CTk):
    """The modern GUI interface."""
    def __init__(self):
        super().__init__()
        self.title("Rigctld Multiplexer Proxy by PD3AN")
        self.geometry("600x420")
        self.resizable(False, False)

        self.proxy_state = ProxyState()
        self.async_loop = None
        self.proxy_thread = None

        self.setup_ui()
        self.load_config()
        self.update_ui_loop()

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- LEFT FRAME: SETTINGS ---
        self.settings_frame = ctk.CTkFrame(self)
        self.settings_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        ctk.CTkLabel(self.settings_frame, text="Proxy Settings", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(15, 10))

        ctk.CTkLabel(self.settings_frame, text="Listen IP:").pack(anchor="w", padx=20)
        self.proxy_ip_entry = ctk.CTkEntry(self.settings_frame)
        self.proxy_ip_entry.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self.settings_frame, text="Listen Port:").pack(anchor="w", padx=20)
        self.proxy_port_entry = ctk.CTkEntry(self.settings_frame)
        self.proxy_port_entry.pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkLabel(self.settings_frame, text="Physical Rigctld IP:").pack(anchor="w", padx=20)
        self.real_ip_entry = ctk.CTkEntry(self.settings_frame)
        self.real_ip_entry.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(self.settings_frame, text="Physical Rigctld Port:").pack(anchor="w", padx=20)
        self.real_port_entry = ctk.CTkEntry(self.settings_frame)
        self.real_port_entry.pack(fill="x", padx=20, pady=(0, 20))

        self.toggle_btn = ctk.CTkButton(self.settings_frame, text="Start Proxy", fg_color="green", hover_color="darkgreen", command=self.toggle_proxy)
        self.toggle_btn.pack(pady=10)

        # --- RIGHT FRAME: STATUS ---
        self.status_frame = ctk.CTkFrame(self)
        self.status_frame.grid(row=0, column=1, padx=(0, 20), pady=20, sticky="nsew")

        ctk.CTkLabel(self.status_frame, text="Live Status", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(15, 10))

        self.backend_status_label = ctk.CTkLabel(self.status_frame, text="Backend: OFFLINE", text_color="gray")
        self.backend_status_label.pack(pady=(10, 20))

        self.client_count_label = ctk.CTkLabel(self.status_frame, text="Connected Clients: 0", font=ctk.CTkFont(weight="bold"))
        self.client_count_label.pack(pady=(5, 5))

        self.client_listbox = ctk.CTkTextbox(self.status_frame, width=220, height=150, state="disabled")
        self.client_listbox.pack(padx=20, pady=(0, 20))

    def load_config(self):
        """Loads settings from JSON or sets defaults if file is missing."""
        config = {
            "proxy_ip": "127.0.0.1",
            "proxy_port": "4532",
            "real_ip": "127.0.0.1",
            "real_port": "4535"
        }
        
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    loaded_config = json.load(f)
                    config.update(loaded_config)
            except Exception as e:
                logging.error(f"Error loading config file: {e}")

        # Populate the UI fields
        self.proxy_ip_entry.insert(0, config["proxy_ip"])
        self.proxy_port_entry.insert(0, config["proxy_port"])
        self.real_ip_entry.insert(0, config["real_ip"])
        self.real_port_entry.insert(0, config["real_port"])

    def save_config(self):
        """Saves current UI fields to JSON file."""
        config = {
            "proxy_ip": self.proxy_ip_entry.get(),
            "proxy_port": self.proxy_port_entry.get(),
            "real_ip": self.real_ip_entry.get(),
            "real_port": self.real_port_entry.get()
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving config file: {e}")

    def toggle_proxy(self):
        if not self.proxy_state.is_running:
            # SAVE SETTINGS AND LOCK UI
            self.save_config()
            
            self.toggle_btn.configure(text="Stop Proxy", fg_color="red", hover_color="darkred")
            self.proxy_ip_entry.configure(state="disabled")
            self.proxy_port_entry.configure(state="disabled")
            self.real_ip_entry.configure(state="disabled")
            self.real_port_entry.configure(state="disabled")
            
            # START PROXY
            self.proxy_thread = threading.Thread(target=self.run_asyncio_loop, daemon=True)
            self.proxy_thread.start()
        else:
            # STOP PROXY AND UNLOCK UI
            self.proxy_state.is_running = False
            self.toggle_btn.configure(text="Start Proxy", fg_color="green", hover_color="darkgreen")
            
            self.proxy_ip_entry.configure(state="normal")
            self.proxy_port_entry.configure(state="normal")
            self.real_ip_entry.configure(state="normal")
            self.real_port_entry.configure(state="normal")
            
            self.backend_status_label.configure(text="Backend: OFFLINE", text_color="gray")

    def run_asyncio_loop(self):
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        
        server = AsyncProxyServer(
            self.proxy_state,
            self.proxy_ip_entry.get(),
            self.proxy_port_entry.get(),
            self.real_ip_entry.get(),
            self.real_port_entry.get()
        )
        self.async_loop.run_until_complete(server.start())
        self.async_loop.close()

    def update_ui_loop(self):
        if self.proxy_state.is_running:
            if self.proxy_state.backend_connected:
                self.backend_status_label.configure(text="Backend: CONNECTED", text_color="green")
            else:
                self.backend_status_label.configure(text="Backend: DISCONNECTED", text_color="orange")

            self.client_count_label.configure(text=f"Connected Clients: {len(self.proxy_state.connected_clients)}")
            
            self.client_listbox.configure(state="normal")
            self.client_listbox.delete("1.0", "end")
            if self.proxy_state.connected_clients:
                for client in self.proxy_state.connected_clients:
                    self.client_listbox.insert("end", f"• {client}\n")
            else:
                self.client_listbox.insert("end", "Waiting for clients...")
            self.client_listbox.configure(state="disabled")
        else:
            self.client_listbox.configure(state="normal")
            self.client_listbox.delete("1.0", "end")
            self.client_listbox.insert("end", "Proxy stopped.")
            self.client_listbox.configure(state="disabled")
            self.client_count_label.configure(text="Connected Clients: 0")

        self.after(500, self.update_ui_loop)

if __name__ == "__main__":
    app = ProxyApp()
    app.mainloop()
