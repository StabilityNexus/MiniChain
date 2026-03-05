# Commands for testing

## Commands for testing

### Test 1: Same Machine, Two Terminals

```bash
# activate the virtual environment
source .venv/bin/activate

# Terminal 1
python main.py --port 9000

# Terminal 2
python main.py --port 9001 --connect 127.0.0.1:9000
```

### Test 2: Two Machines, Same LAN

```bash
# Machine A (Ex: 192.168.1.10)
python3 main.py --port 9000 --mine

# Machine B (Ex: 192.168.1.20)
python3 main.py --port 9001 --connect 192.168.1.10:9000
```

**Follow the below steps for test 2**

**find the IP addresses**

```bash
# run this both on machine A and machine B
ip addr | grep "inet " | grep -v 127.0.0.1
# or
hostname -I
```

### Machine A (WSL + Windows Port Forwarding)

**WSL: Start the node (miner)**

```bash
cd /path/to/minichain  # adjust to your project directory
source .venv/bin/activate
python3 main.py --port 9000 --mine
```

**Windows PowerShell (Admin): Forward port 9000 to WSL**

```powershell
$wslIp = wsl hostname -I
$wslIp = $wslIp.Trim()
netsh interface portproxy add v4tov4 listenport=9000 listenaddress=0.0.0.0 connectport=9000 connectaddress=$wslIp
netsh advfirewall firewall add rule name="MiniChain Node" dir=in action=allow protocol=tcp localport=9000
netsh interface portproxy show all
```

### Machine B (Same LAN)

**Test connectivity first**:

```bash
ping 192.168.137.10
```

**Test port first:**

```bash
# linux
nc -zv 192.168.137.10 9000

# windows
Test-NetConnection -ComputerName 192.168.137.10 -Port 9000
```

**If ping works, start the node for (Linux/macOS):**

```bash
cd minichain
source .venv/bin/activate
python3 main.py --port 9001 --connect 192.168.137.10:9000
```

**Windows:**

```powershell
cd minichain
python main.py --port 9001 --connect 192.168.137.10:9000
```


