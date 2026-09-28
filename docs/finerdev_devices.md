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
| `synth.__synth` | `synth` | `EtherScpi` | `(addr, port=5025)` | `freq()`, `amp()`, `output()`（`self.open()`をコンストラクタ内で呼ぶ） |
| `multiplier.__mp` | `mp` | `EtherScpi` | `(addr, port=5025)` | `on()`, `off()`, `get()`（`self.open()`をコンストラクタ内で呼ぶ） |

非公開/重複ファイル: `chopper.mirror_server.Optserver`・`chopper.opt_server.OPTserver`はサーバ側実装（`__all__`に含まれず非公開）。`sourcemeter.sourcemeters_pl.SoureMeterPl`と`drs4.drs4_client.DRS4client`は、パッケージの`__init__.py`が別ファイル（`__`プレフィックス版）からインポートしているため未使用の重複ファイル。`synth`は2026-09-28時点で手続き型モジュール（`synth_py2/3.py`）からクラス版（`__synth.py`の`synth`クラス）に更新済み（旧・下記食い違い#5は解消）。`multiplier`パッケージも同日追加された新規モジュール。

## design.md の前提との食い違い（要注意点）

1. **コンストラクタが通信する**: `DRS4client`, `OPTclient`, `synth`, `mp` は`__init__`内で即座に`openTCP()`/`self.open()`を呼ぶ。`ifSynth`も`__init__`で直接`serial.Serial()`を開く（design.md §1.2の前提「コンストラクタでは通信を行わない」に反する）。ただし`EtherScpi`系の`open()`/`write()`/`read()`は失敗時に例外を投げず`False`を返すだけなので、実際にコンストラクタが例外で落ちることは少ない（＝`layout.py`の読み込み自体が失敗する事態にはなりにくい）。
2. **`ifSynth`はimport時に副作用あり**: モジュールレベルで`synth = [ifSynth('/dev/ttyACM0'), ifSynth('/dev/ttyACM1')]`を実行し、`os.system('sudo chmod 777 ...')`も呼ぶ。
3. **`open`/`close`という名前ではないクラスがある**: `DRS4client`/`OPTclient`は`openTCP`/`closeTCP`。design.md §6.2の`hasattr(instance, "open")`による自動判定では検出されない。
4. **`open()`が例外ではなくboolを返す**: `EtherScpi.open()`（→`EtherGpib`→`SoureMeter2400`/`loatt`/`synth`/`mp`が継承）は失敗時に`False`を返すのみで例外を投げない。
5. ~~`synth`モジュールはクラスではない~~ → 2026-09-28時点で解消済み（`finerdev.synth.synth`クラスが追加された）。
6. **`FINER_LOGDIR`環境変数が必須**: `loatt`, `mp`, `synth`, `SoureMeter2400`, `SourceMeter2450`はモジュールレベルで`os.environ['FINER_LOGDIR']`を読む。**現在このマシンでは未設定**であり、設定しないままこれらのモジュールをimportすると`KeyError`で即座に失敗する。devguiサーバーの実行環境（systemdユニット等）で必ず設定する必要がある。
7. **`loatt.set()`のバグ**: 範囲外の値で`raise("...")`（文字列を直接raise）しており、Python 3では`TypeError: exceptions must derive from BaseException`になる（意図した`ValueError`にはならない）。devgui側のエラー表示は例外の型とメッセージをそのまま出す設計（design.md §10）なので、この場合は分かりにくいエラーが表示される点に注意。

**方針（2026-09-28時点で確認済み）**: 今回のdevgui実装ではこれらを気にせずdesign.mdの理想形通りに進める。実機接続時はlayout.py側の薄いアダプタ関数/クラスで吸収する。

## device_list.txt との対応（実機配置設計）

`docs/device_list.txt`（ユーザー用意）に列挙された、実際に生成すべきインスタンスとdevgui上の配置。将来 `examples/layout_example.py` をこれに基づいて実装する。

| tab (Category) | instance名 (Device.name) | 生成コード | bus |
|---|---|---|---|
| MX | SourceMeter1 | `SoureMeter2400(devid=25, ipAddr="prologix")` | `"gpib-prologix"` |
| MX | SourceMeter2 | `SoureMeter2400(devid=18, ipAddr="prologix")` | `"gpib-prologix"` |
| MX | SourceMeter3 | `SourceMeter2450(ipAddr="finer-sm3")` | `None`（LAN、独立） |
| MX | SourceMeter4 | `SourceMeter2450(ipAddr="finer-sm4")` | `None`（LAN、独立） |
| LO | LO att1 | `loatt(devid=8)` | `"gpib-prologix"` |
| LO | LO att2 | `loatt(devid=9)` | `"gpib-prologix"` |
| LO | LO att3 | `instance=None`（`not_installed`。addr未定のため） | — |
| LO | LO att4 | `instance=None`（`not_installed`。addr未定のため） | — |
| LO | synth (B4+5) | `synth(addr="finer-sg45")` | `None`（LAN、独立） |
| LO | synth (B6+7) | `synth(addr="finer-sg67")` | `None`（LAN、独立） |
| LO | multiplier | `mp(addr="finer-mp")` | `None`（LAN、独立） |

**バス設計の要点**: `SourceMeter1/2`と`LO att1/2`（将来`LO att3/4`も）は全て物理GPIB-Ethernetブリッジ`prologix`を共有するため、同一`bus`名（`"gpib-prologix"`）を明示指定する。指定しないとデバイス名ごとに別スレッドから同じ物理アダプタへ同時アクセスしてしまい、GPIB通信が破損する危険がある。LAN接続の各デバイス（SourceMeter3/4, synth×2, multiplier）はそれぞれ独立したTCPソケットなので`bus`指定不要（デフォルトのデバイス名別バスでよい）。

**未確認の前提**: GPIBデバイスの`ipAddr="prologix"`は`loatt`モジュール内のハードコード定数`_IPADDR_EGPIB`から類推した値。`SoureMeter2400`はこの値を内部で持たず呼び出し側が渡す必要があるため、実際に同じホスト名でよいか要確認。

**`LO att3`/`LO att4`（未接続プレースホルダー）**: `mode=NC`かつ`addr=*`で接続情報が未定のため、実機インスタンスを生成できない。ユーザーの希望により、design.md を拡張して `Device(instance=None)` を「未実装（`not_installed`）」状態のプレースホルダーとして表示できるようにした（design.md §4.2, §6.1, §6.3, §11 に追記済み）。接続先が決まり次第、`instance=loatt(devid=...)` に差し替える想定。
