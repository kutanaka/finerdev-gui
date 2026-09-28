# finerdev パッケージ デバイスクラス一覧

`~/.local/lib/python3.14/site-packages/finerdev`（v0.0.1、"finer devices"）に定義されている実デバイス制御クラスの調査結果。devgui本体（`devgui/widgets.py` 等）はこれらに依存しないが、`examples/mock_devices.py`（design.md §14 Step 2）や実機接続用の`layout.py`を書く際の参考情報として記録する。

## クラス一覧

| モジュール | クラス | 継承 | コンストラクタ | 主なメソッド |
|---|---|---|---|---|
| `ethergpib.etherscpi` | `EtherScpi` | — | `(ipAddr, port)` | `open()`, `close()`, `write()`, `sendCommands()`, `read()` |
| `ethergpib.ethergpib` | `EtherGpib` | `EtherScpi` | `(devid, ipAddr)` | `dev()`, `read()`（override）＋継承の`open/close` |
| `sourcemeter.__sourcemeters_pl` | `SoureMeter2400`（※クラス名タイポ） | `EtherGpib` | `(devid, ipAddr)` | `open()`, `output()`, `meas()`, `setV()`, `measIV()`, `get_message()`, `get()` |
| `sourcemeter.__sourcemeters_scpi` | `SourceMeter2450` | `EtherScpi` | `(ipAddr)` | `open()`, `meas()`, `output()`, `setV()`, `measIV()`, `get_message()`, `get()` |
| `loatt.__loatt` | `loatt` | `EtherGpib` | `(devid)` | `set(att)`, `get()`（設計書の想定に最も近い） |
| `loatt.__loatt_table` | `loattTable` | — | `(tableFile='')` | `getAtt(freq)`（デバイスではなくテーブル参照用の補助クラス） |
| `drs4._drs4_client` | `DRS4client` | — | `(hostname)` | `openTCP()`, `closeTCP()`, `sendCommand()`＋動的付与の`start`,`stop`,`tune` |
| `chopper._opt_client` | `OPTclient` | — | `(hostname)` | `openTCP()`, `closeTCP()`, `sendCommand()`＋動的付与の`setR`,`setS` |
| `ifsynth.ifsynth` | `ifSynth` | — | `(port)` | `on()`, `off()`, `query()`, `freq()`（open/closeなし） |
| `fw` | `FW` | — | `()` | `newobs()`, `endobs()`, `start()`, `stop()`, `meas()`（C拡張ラッパー、open/close/set/getなし） |
| `synth.synth_py3` | （クラスなし） | — | — | モジュール直下の関数`connect()`/`close()`/グローバルsocket（**クラスではない**） |

非公開/重複ファイル: `chopper.mirror_server.Optserver`・`chopper.opt_server.OPTserver`はサーバ側実装（`__all__`に含まれず非公開）。`sourcemeter.sourcemeters_pl.SoureMeterPl`と`drs4.drs4_client.DRS4client`は、パッケージの`__init__.py`が別ファイル（`__`プレフィックス版）からインポートしているため未使用の重複ファイル。

## design.md の前提との食い違い（要注意点）

1. **コンストラクタが通信する**: `DRS4client`, `OPTclient`は`__init__`内で即座に`openTCP()`を呼ぶ。`ifSynth`も`__init__`で直接`serial.Serial()`を開く（design.md §1.2の前提「コンストラクタでは通信を行わない」に反する）。
2. **`ifSynth`はimport時に副作用あり**: モジュールレベルで`synth = [ifSynth('/dev/ttyACM0'), ifSynth('/dev/ttyACM1')]`を実行し、`os.system('sudo chmod 777 ...')`も呼ぶ。
3. **`open`/`close`という名前ではないクラスがある**: `DRS4client`/`OPTclient`は`openTCP`/`closeTCP`。design.md §6.2の`hasattr(instance, "open")`による自動判定では検出されない。
4. **`open()`が例外ではなくboolを返す**: `EtherScpi.open()`（→`EtherGpib`→`SoureMeter2400`/`loatt`が継承）は失敗時に`False`を返すのみで例外を投げない。
5. **`synth`モジュールはクラスではない**: `Device(instance=...)`に直接渡せない。使うなら薄いラッパークラスが必要。

**方針（2026-09-28時点で確認済み）**: 今回のdevgui実装ではこれらを気にせずdesign.mdの理想形通りに進める。実機接続時はlayout.py側の薄いアダプタ関数/クラスで吸収する。
