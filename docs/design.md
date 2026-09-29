# devgui 設計書

Ubuntuサーバーに接続された複数の計測器・デバイスを、LAN内のブラウザから操作・監視するWebアプリケーション。

本書はClaude Codeによる実装のための設計書である。記載のない細部は実装者が妥当に判断してよいが、「未決定事項」に挙げた点や、本書の方針と矛盾する判断が必要な場合は、実装前に確認すること。

---

## 1. 目的と前提

### 1.1 目的

- 既存のPythonデバイス制御ライブラリを、コードを変更せずにWeb GUIから操作できるようにする。
- GUIの構成（どの部品がどの関数を呼ぶか）を、1つのPythonファイル（`layout.py`）で手軽に宣言・変更できるようにする。
- デバイスや部品の追加時に、HTML/JavaScriptの編集を不要にする。

### 1.2 既存ライブラリ（ユーザーが手動で実装・保守する。本プロジェクトでは変更しない）

- 1台のデバイスに対し、デバイスの種類に応じたクラスのインスタンスが1つ対応する。
- 各クラスは概ね `open(self)` / `close(self)` / `set(self, value)` / `get(self)` などのメソッドを持つ。ただし全メソッドが揃っているとは限らない。
- 通信はPyVISA等によるGPIBまたはネットワーク（TCP/IP）。
- メソッドはすべて同期（ブロッキング）であり、スレッドセーフではないものとして扱う。
- コンストラクタでは通信を行わない前提とする（通信開始は `open()`）。

### 1.3 利用環境

- サーバー: Ubuntu、Python 3.10以上
- クライアント: LAN内のPC・タブレット等のモダンブラウザ
- 認証: 不要（LAN内の誰でも閲覧可能）。操作は後述の「操作権」を持つ1クライアントのみ。

---

## 2. 技術スタック

| 役割 | 採用技術 |
|---|---|
| Webフレームワーク | FastAPI |
| ASGIサーバー | uvicorn |
| リアルタイム通信 | WebSocket（FastAPI標準） |
| フロントエンド | 素のHTML / CSS / JavaScript（ビルド工程なし、FastAPIから静的配信） |
| テスト | pytest, pytest-asyncio |
| 常駐化 | systemd |

フロントエンドにフレームワークやビルド工程を入れないのは、保守者がJavaScriptのツールチェーンを持たずに済むようにするため。画面はすべて `GET /api/layout` のJSONから動的に生成する。

---

## 3. ディレクトリ構成

```
devgui/
├── pyproject.toml
├── README.md
├── devgui/
│   ├── __init__.py          # Category, Device, 各部品クラスを公開
│   ├── __main__.py          # CLIエントリポイント
│   ├── widgets.py           # Category / Device / 部品の定義
│   ├── layout_loader.py     # layout.py の読み込みと検証
│   ├── settings.py          # 設定値
│   ├── runtime/
│   │   ├── bus.py           # バスごとのワーカースレッドとキュー
│   │   ├── devices.py       # デバイスのライフサイクルと状態管理
│   │   ├── poller.py        # Display のポーリングとログバッファ
│   │   └── operator.py      # 操作権と強制取得の状態機械
│   ├── server.py            # FastAPIアプリ、REST、WebSocket
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── examples/
│   ├── mock_devices.py      # テスト・動作確認用のダミーデバイス
│   └── layout_example.py
├── tests/
└── deploy/
    └── devgui.service       # systemdユニット例
```

起動方法:

```
python -m devgui --layout /path/to/layout.py --host 0.0.0.0 --port 8000
```

---

## 4. レイアウト定義（layout.py）

### 4.1 概要

ユーザーは `layout.py` を書き、モジュールレベル変数 `layout`（`Category` のリスト）を定義する。任意で `settings`（`devgui.Settings`）も定義できる。

```python
# layout.py
from devgui import Category, Device, Button, NumberInput, Toggle, Select, TextInput, Display
from mylib import PowerSupply, Multimeter

psu1 = PowerSupply("GPIB0::5::INSTR")
psu2 = PowerSupply("GPIB0::6::INSTR")
dmm1 = Multimeter("TCPIP0::192.168.1.20::INSTR")

# 同じ機種のテンプレートは普通のPython関数で書く
def psu_widgets(dev):
    return [
        NumberInput("電圧設定", call=dev.set, unit="V", min=0, max=30, step=0.001),
        Display("出力電圧", call=dev.get, unit="V", poll=1.0, fmt="{:.3f}"),
    ]

layout = [
    Category("電源", [
        Device("PSU1", psu1, bus="gpib0", widgets=psu_widgets(psu1)),
        Device("PSU2", psu2, bus="gpib0", widgets=psu_widgets(psu2)),
    ]),
    Category("測定器", [
        Device("DMM1", dmm1, bus="lan-dmm1", widgets=[
            Display("測定値", call=dmm1.get, poll=0.5),
            Button("リセット", call=lambda: dmm1.set("*RST"), confirm=True),
        ]),
    ]),
]
```

### 4.2 構成要素

**`Category(title, devices)`**
画面上のタブ1つに対応する。`devices` は `Device` のリスト。

**`Device(name, instance, *, bus=None, widgets=(), auto_open=True, instance_repr=None)`**
タブ内のパネル1つに対応する。

- `name`: 表示名かつ識別子。全体で一意であること（重複は起動時エラー）。
- `instance`: 既存ライブラリのインスタンス。`None` を指定すると「未実装（プレースホルダー）」デバイスとして扱う（6.1参照）。将来接続予定だが現時点では実機・インスタンスを用意できないデバイスを、タブ上に場所だけ確保しておくための機能。
- `bus`: 通信を直列化する単位の名前（5章）。同じGPIBバス上のデバイスには同じ名前を付ける。`None` の場合はデバイス名を用いる（そのデバイス専用）。
- `widgets`: 部品のリスト。表示順はリスト順。
- `auto_open`: 起動時に `open()` を自動で呼ぶか（6章）。`instance=None` の場合は無視される。
- `instance_repr`: `instance` を実際にどう構築したかを示す文字列（例: `"loatt(devid=8)"`）。`instance` は `layout.py` 自身がこの呼び出しより前に構築済みのため、devgui側には実際のコンストラクタ呼び出しを知る手段がない。コマンドログ（8.2）のインスタンス生成行で使われ、省略時は `"{クラス名}(...)"` という汎用表示にフォールバックする。

### 4.3 部品（widgets）

全部品は第1引数に表示ラベル、キーワード引数 `call` に呼び出す関数を取る。`call` には任意の callable（バウンドメソッド、lambda、自作関数）を渡せる。フレームワークは `call` の中身に関知しない。

| 部品 | 呼び出し | 主なオプション |
|---|---|---|
| `Button(label, call, confirm=False)` | `call()` | `confirm=True` で実行前に確認ダイアログ |
| `NumberInput(label, call, unit=None, min=None, max=None, step=None, default=None, type=float, get=None)` | `call(value)`。`type` で `float` / `int` に変換 | 入力欄＋「設定」ボタン。min/max はサーバー側でも検証。`get`未指定（既定）では「設定」は常に有効。`get`を指定すると、`DigitInput`と同じget/setの保留値管理になる: 入力値が直前の確定値と異なる間は青字になり、その間だけ「設定」と隣の「キャンセル」ボタンが有効になる（「キャンセル」は入力値を確定値に戻すだけでサーバーには問い合わせない）。`get`はデバイスが`connected`になるたびに呼ばれて初期値を反映し、「設定」成功後にも呼ばれて実際にデバイスが取った値を反映する |
| `DigitInput(label, call, digits=4, min=None, max=None, default=0, get=None)` | `call(int)`（「設定」ボタン押下時のみ） | 桁ごとに増減ボタンを持つ10進整数入力（既定4桁）。桁を変更すると値が青字になり未確定であることを示し、その間だけ「設定」と隣の「キャンセル」ボタンが有効になる（「キャンセル」は表示を直前の確定値に戻すだけでサーバーには問い合わせない）。「設定」で確定すると黒字に戻る。min/max はサーバー側でも検証し、拒否された場合は元の値に戻る。`get`を指定すると、デバイスが`connected`になるたび（起動時のauto_open、または再接続時）と「設定」成功後に呼ばれ、その結果を表示中の値として反映する（Toggle/Selectと異なり実機からの読み戻しに対応）。アッテネータの設定値のような、連続量ではない機器コードの入力を想定 |
| `Toggle(label, call, default=False, off_call=None)` | `call(bool)`。`off_call`指定時は `call()`／`off_call()` のどちらか一方（引数なし） | 切り替えた時点で呼ぶ。画面上の見た目は左右にスライドするスイッチ（11章）。`off_call`は、実機がon/offを1つの`set(bool)`ではなく別々の無引数メソッド（例: `mp.on()`/`mp.off()`）として持つ場合に指定する。指定すると`call`は「on」側、`off_call`は「off」側の呼び出しとして扱われ、コマンドログ（8.2）には実際に呼ばれたメソッド名がそのまま出る |
| `Select(label, call, options)` | `call(選択値)` | `options` はリスト、または `{表示名: 値}` の辞書 |
| `TextInput(label, call, default="")` | `call(str)` | 入力欄＋「送信」ボタン |
| `Display(label, call, unit=None, poll=1.0, fmt=None, visible_rows=5, max_rows=50)` | `call()` を `poll` 秒ごと | 戻り値を時刻付きでログ表示（8章） |
| `SourceMeasure(label, call, *, output, meas, sweep, get, message=None, min=None, max=None, step=None, sweep_default=(0, 0, 0), voltage_unit="V", current_unit="A")` | 下記 | ソースメータ用の複合部品。部品間に依存関係（出力ONの間だけ測定可、測定結果で電圧欄と電流欄を更新）があるため1部品にまとめている。下記参照 |

**`SourceMeasure`** の画面と動作:

- 画面上の電圧・電流（入力欄、sweepパラメータ、電流欄、グラフの軸）はすべて `voltage_unit`（`"V"`/`"mV"`/`"uV"`）・`current_unit`（`"A"`/`"mA"`/`"uA"`/`"nA"`）の単位で表示・入力する。`min`/`max`/`step`/`sweep_default` もこの単位で指定する。デバイスのメソッドには常にV/Aで渡し、V/Aで受け取る（例: `voltage_unit="mV"` で 2 と入力 → `setV(0.002)`）。
- 「電圧設定」入力欄＋「meas」「キャンセル」ボタン。入力欄は `NumberInput(get=...)` と同じく、確定値と異なる間は青字になり「キャンセル」で確定値に戻る。「meas」は `call(電圧)` → `meas()` → `get()` を1つのバスジョブとして順に実行する。
- 「電流」表示欄（ユーザーは編集不可）。
- 「出力ON/OFF」スイッチ: `output(bool)` を呼ぶ。
- 「sweep」: vstart / vend / vstep の入力欄と「sweep」ボタン。`sweep(vstart, vend, vstep)` → `get()` を実行する。
- 「I-V」グラフ（横軸 電圧、縦軸 電流）と「PDF」ボタン。実機のsweepは10秒程度かかるため、実行中（`meas`/`sweep` の実行状態もサーバー側で保持し全クライアントに配信する）はグラフ上に「sweep in progress... N s」と経過秒数を重ねて表示し、パネルの操作を無効にする。PDFボタンは最後のsweepのグラフをPDFでダウンロードする（操作権不要）。
- `get()` の戻り値は `[電流, 電圧, ...]`（meas）またはその行の並び（sweep、行数不定）。list でも numpy 配列でもよく、3列目以降は無視する。meas では唯一の行、sweep では最後の行を最新の電流・電圧として電流欄と電圧欄（確定値）に反映する。
- 「meas」「sweep」は出力ONの間だけ有効（サーバー側でも拒否する）。電圧・vstart・vend は min/max をサーバー側でも検証し、vstep > 0、vstart <= vend も検証する。
- 各デバイスメソッドが厳密に `False` を返した場合は失敗として扱う（finerdevの慣習）。`message`（例: finerdevの `get_message`）を指定すると、そのとき理由の取得に呼ぶ。
- 出力状態・最後の測定値・最後のsweepはサーバー側で保持し、`widget_value` で全クライアントに配信する（再読込や別端末でも同じ表示）。デバイスの `open()` 成功時は、実機のリセットに合わせて出力状態をOFFに戻す。
- コマンドログ（8.2）には、実際に呼ばれた各メソッド（`setV(0.002) -> True` など）を1行ずつ記録する。

- 各部品には、起動時に一意なID（例: `"{device_name}:{index}"`）を割り振る。
- `call` の戻り値は、`Display` 以外では無視する（成功表示のみ）。例外はエラーとして扱う（10章）。
- 操作系部品（Display以外）は、操作権を持つクライアントのみ実行可能（9章）。
- `Toggle` と `Select` は、実機の状態を読み戻す機能を持たない（画面上の値は最後に送った値）。
- `SourceMeasure` は `/api/call` の body に `"action"`（`"output"` / `"meas"` / `"sweep"`）を付ける（7.2）。

### 4.4 読み込みと検証

- `importlib` でファイルパスから `layout.py` を読み込む。`layout.py` のあるディレクトリを `sys.path` に追加し、ユーザーのライブラリをimportできるようにする。
- 検証項目: `layout` の存在と型、デバイス名の一意性、`call` が callable であること、`poll > 0`、`visible_rows <= max_rows` など。
- 検証エラー時は、どの箇所が問題かを明示するメッセージを出して起動を中止する。

---

## 5. 実行モデル（通信の直列化とスレッド）

### 5.1 バスワーカー

- `bus` 名ごとに、ワーカースレッド1本と優先度付きキュー1つを持つ。
- デバイスのメソッド呼び出し（`open` / `close` / 部品の `call` / ポーリング）は、すべて該当バスのキューに投入し、ワーカーが1件ずつ実行する。これにより同一バス上の通信は必ず直列化される。
- asyncio側からは `concurrent.futures.Future` を `asyncio.wrap_future` で待つ。イベントループはブロックしない。
- 優先度: ユーザー操作・open/close を、ポーリングより優先する。

### 5.2 ポーリングの詰まり防止

- 各 `Display` について、前回のポーリングが未完了なら次のポーリングを投入しない（スキップする）。遅い機器でキューが膨らむのを防ぐ。

### 5.3 タイムアウト

- フレームワーク側では通信タイムアウトを強制しない（既存ライブラリの責任とする）。
- ただし1回の呼び出しが長時間（設定値、既定30秒）を超えた場合は警告をログに出す。

---

## 6. デバイスのライフサイクル

### 6.1 状態

各デバイスは次の状態を持つ。

- `disconnected`: 未接続
- `connecting` / `disconnecting`: open/close 実行中
- `connected`: 接続中
- `error`: open失敗など（エラーメッセージを保持）
- `not_installed`: `instance=None`（4.2）で宣言されたプレースホルダー。常にこの状態のままであり、他の状態には遷移しない。

`open()` を持たないデバイスは常に `connected` として扱う。ただし `instance=None` の場合は例外的に `not_installed` とする（`connected` にはしない）。

### 6.2 open / close の判定

- フレームワークは `hasattr(instance, "open")` および `callable` で有無を判定し、インスタンスの `open()` を呼ぶだけとする。
- 親クラスの `open()` を別途呼ぶ、`super()` をたどる、などの継承関係への関与は一切しない。親の処理が必要な場合は各クラスの実装側で行う。
- `close()` も同様。

### 6.3 起動時の自動open

- サーバー起動時、`auto_open=True` かつ `open()` を持つデバイスについて `open()` を呼ぶ（バスキュー経由で直列に）。`not_installed`（`instance=None`）のデバイスは対象外。
- あるデバイスの失敗は他のデバイスの起動を止めない。失敗したデバイスは `error` 状態になる。
- 自動openは操作権と無関係に実行する。

### 6.4 手動open / close

- `open()` / `close()` を持つデバイスのパネルヘッダーに、「接続」「切断」ボタンと状態表示を自動で配置する。`layout.py` での記述は不要。
- `error` 状態からは「接続」で再試行できる。
- 手動の接続・切断は操作権が必要。

### 6.5 状態と部品の連動

- `connected` 以外の状態では、そのデバイスの `Display` のポーリングを停止し、操作系部品を無効化する。

### 6.6 終了時

- サーバー停止時（FastAPIのlifespan終了）に、`connected` の各デバイスについて `close()` を呼ぶ。失敗はログに残して続行する。

---

## 7. サーバーAPI

### 7.1 クライアントの識別

- ブラウザはページ読み込み時にWebSocket（`/ws`）へ接続し、サーバーから `client_id` を受け取る。
- 以降のREST呼び出しには `X-Client-Id` ヘッダーを付ける。操作系APIにはさらに `X-Operator-Token` ヘッダーを付ける。
- クライアントの表示名は、接続元IPアドレスと、逆引きできればホスト名（結果はキャッシュ、逆引きはタイムアウト付きで非同期に）。

### 7.2 REST

| メソッド | パス | 説明 | 操作権 |
|---|---|---|---|
| GET | `/api/layout` | 画面構成のJSON | 不要 |
| POST | `/api/call/{widget_id}` | 部品の関数を実行。body: `{"value": ...}`（`SourceMeasure` は `{"action": "output"\|"meas"\|"sweep", "value": ...}`、sweepの value は `{"vstart", "vend", "vstep"}`） | 必要 |
| GET | `/api/widgets/{widget_id}/iv.pdf` | `SourceMeasure` の最後のsweepのI-VグラフPDF（未測定なら404） | 不要 |
| POST | `/api/devices/{name}/open` | 手動接続 | 必要 |
| POST | `/api/devices/{name}/close` | 手動切断 | 必要 |
| POST | `/api/operator/acquire` | 操作権取得（空いている場合） | — |
| POST | `/api/operator/release` | 操作権解放 | 必要 |
| POST | `/api/operator/request` | 強制取得の要求 | — |
| POST | `/api/operator/request/{id}/cancel` | 要求の取り消し（要求者のみ） | — |
| POST | `/api/operator/request/{id}/respond` | 許可/拒否。body: `{"accept": bool}` | 必要（保持者） |
| POST | `/api/priority/get` | 外部プログラムによる取得（9.5）。body: `{"block", "force", "name"}` | `priority_hosts` から |
| POST | `/api/priority/release` | 外部保持の解放とブロック解除。body: `{"token", "force"}`（9.5） | `priority_hosts` から |

- 操作権のない操作系呼び出しは `403` を返す。
- `/api/call` は実行完了まで待ってから応答する。例外時は `{"ok": false, "error": "..."}` を返す。

### 7.3 WebSocket（サーバー → クライアント）

すべて `{"type": ..., ...}` 形式のJSON。

- `hello`: `client_id` の通知
- `snapshot`: 接続直後に送る全状態（デバイス状態、全Displayのログ、操作コマンドログ、操作権の状態）
- `device_state`: デバイス状態の変化
- `display_entry`: Displayのログ1行追加
- `command_log`: 操作コマンドログ1行追加（8.2）
- `operator_state`: 操作権の保持者（表示名）、要求中かどうか、など
- `operator_granted`: 対象クライアントへのトークン通知
- `operator_revoked`: 旧保持者への失効通知（理由付き）
- `takeover_request`: 保持者への強制取得要求（要求者の表示名、残り秒数、request_id）
- `takeover_result`: 要求者への結果（granted / rejected / cancelled）
- `notice`: 汎用のトースト通知

クライアント → サーバー方向のメッセージは原則使わない（操作はRESTで行う）。生存確認のping/pongのみ。

---

## 8. ログ表示

### 8.1 Display のログ

- サーバー側で部品ごとに `collections.deque(maxlen=max_rows)`（既定50）のリングバッファを持つ。古い行は自動的に破棄される。
- 各行: `{"t": ISO8601タイムスタンプ（サーバー時刻、ミリ秒）, "value": 表示文字列, "error": bool}`
- 値の文字列化: `fmt` 指定時は `fmt.format(value)`、なければ `str(value)`。単位は表示側で付加する。
- 時刻は `get()` の実行完了時点のサーバー時刻とする。
- `get()` が例外を投げた場合は、例外メッセージを `error: true` の行として記録する。
- 画面表示:
  - 時刻は `HH:MM:SS.mmm` 形式。
  - 新しい行を上に積む。
  - 高さは `visible_rows`（既定5）行分で、超えた分はスクロール。
  - ユーザーがスクロールして過去の行を見ている間は、新しい行が来ても表示位置を動かさない（先頭にいるときのみ追従）。
  - エラー行は赤字。
- ブラウザを開き直した場合や別端末から開いた場合も、`snapshot` で直近のログを表示する。

### 8.2 操作コマンドログ

- 画面下部に、全タブ・全カテゴリ共通の固定表示エリアを1つ設ける（個々のDeviceパネルには属さない）。
- 記録対象: デバイスインスタンスの生成（起動時、`layout.py` が構築した `instance`。生成コマンドそのものは`layout.py`側のコードであり devgui からは見えないため、`"{name} = {クラス名}(...)"` という形で記録するに留める）、`open()`/`close()`（自動open・手動どちらも）、部品の `call` 実行、`get` による再読出し（起動時・設定成功後）。Displayのポーリングはここには含めない（8.1のログのみに出る - ポーリングは「アクション」ではないため）。
- サーバー側で全体共通の `collections.deque(maxlen=200)` のリングバッファを持つ。
- 各行: `{"t": ISO8601タイムスタンプ（サーバー時刻、ミリ秒）, "device": デバイス名, "text": 実行内容を示す文字列, "error": bool}`。`text` は実際に呼ばれた関数名と引数、成功時は戻り値、失敗時は例外の型とメッセージを含む（例: `"LO att1.set(1234) -> None"`、`"PSU1.set(999)  # ValueError: value 999 is above max 10"`）。
- 画面表示は8.1と同様: `HH:MM:SS.mmm` 形式の時刻、新しい行を上に積む、エラー行は赤字。
- ブラウザを開き直した場合や別端末から開いた場合も、`snapshot` で直近のログを表示する。

---

## 9. 操作権（排他制御）

### 9.1 方針

- サーバー全体で操作権は1つ（デバイス個別ではない）。
- 閲覧（タブ切り替え、ログ・状態の表示）は常に全員可能。
- 操作権を持つクライアントのみ、操作系部品と接続・切断ボタンを使える。それ以外では操作系UIをグレーアウトする。
- 判定は必ずサーバー側でトークンにより行う。UIの無効化は補助にすぎない。

### 9.2 取得と保持

- 空いていれば `acquire` で即取得。ランダムなトークン（`secrets.token_urlsafe`）を発行する。
- トークンは `sessionStorage` に保存し、同じタブのリロード後にWebSocket再接続した場合は保持を継続できる（猶予時間内に限る）。
- 画面上部に、現在の保持者の表示名と、自分が保持者かどうかを常に表示する。

### 9.3 自動解放

- 手動解放（「操作権を解放」ボタン）。
- 保持者のWebSocket切断から猶予時間（既定30秒）以内に再接続がなければ解放。
- 最後の操作から無操作タイムアウト（既定10分）で解放。操作とは、操作系APIの呼び出しを指す。
- 解放時は全クライアントへ `operator_state` を送る。

### 9.4 強制取得

状態機械として実装する（`runtime/operator.py`）。時刻は注入可能なクロックを使い、テストで時間を進められるようにする。

1. 操作権のないクライアントが `request` を送る。
   - 保持者がいなければ、通常の取得として即座に付与する。
   - 既に処理中の要求があれば `409`（「他の要求を処理中」）。
   - 要求者がクールダウン中なら `429`。
2. サーバーは保持者に `takeover_request` を送る。保持者の画面には、要求者の表示名と10秒（既定）のカウントダウン付きダイアログを表示し、「許可」「拒否」を選ばせる。要求者の画面には待機表示と「取り消し」ボタンを出す。
3. 結果:
   - **許可**、または**待機時間内に応答なし**（保持者が切断中の場合を含む）: 操作権を要求者へ移譲する。旧保持者のトークンを無効化し `operator_revoked`（「操作権が〇〇に移りました」）を送る。要求者に `operator_granted` と `takeover_result: granted` を送る。
   - **拒否**: 要求者に `takeover_result: rejected` を送る。同じ要求者（client_idとIPの両方で判定）には再要求のクールダウン（既定30秒）を設ける。
   - **要求者による取り消し** または **要求者の切断**: 要求を破棄し、保持者のダイアログを閉じる。
4. 移譲の瞬間に、旧保持者の操作で既に実行中・キュー投入済みのものは最後まで実行する。移譲後に届いた旧保持者の操作は `403` で拒否する。
5. 保持者が要求処理中に自ら解放した場合は、そのまま要求者に付与する。

要求者の待機表示には、応答待ち時間（`takeover_wait`）のカウントダウンを表示する（`request` の応答に `wait_seconds` を含める）。

### 9.5 外部プログラムからの取得とブロック

測定スクリプトなど、GUIの外のプログラムが操作権を取得・ブロックできる。クライアントは標準ライブラリのみの `devgui.priority` モジュール:

```python
from devgui.priority import priority

priority("get", block=True, force=True, name="IV測定スクリプト")
try:
    ...
finally:
    priority("release")
```

シェルからは `python -m devgui.priority get --block --force` / `python -m devgui.priority release --force`（スクリプト異常終了後の解除にも使う）。シェルのコマンドは毎回別プロセスのため、`--force` なしの `release` には `get` が表示したトークンを `--token` で渡す。

- `force=True`: 誰が保持していても無条件で即時取得する。GUIの旧保持者には `operator_revoked`（「操作権が〇〇に強制取得されました」）を送り、処理中の強制取得要求は破棄して要求者に `takeover_result: rejected` を送る。`force=False` では、GUIのクライアントが保持中なら `409` で失敗する（空き、または既に外部保持中なら取得できる）。
- `block=True`: `release` するまで、GUIからの `acquire` / `request` をすべて即時 `423` で却下する。画面上部には「〇〇 (外部) [ブロック中]」と表示し、「操作権を要求」ボタンを無効にする。
- `block=False` の外部保持は、GUIからの `request` に応答する者がいないため、通常どおり待機時間の経過後に要求者へ移る。
- 外部保持にはWebSocketがないため、無操作タイムアウト・切断猶予による自動解放はない。`release`（ブロックも解除）でのみ終わる。
- `release`（`force=False`）: `get` した主体のみが解放できる。`get` の応答のトークンで判定し、`devgui.priority` は同じプロセス内の直前の `get` のトークンを自動で送る（`token=` で明示も可）。トークンが違えば `403`、外部保持中でなければ `409`。強制取得要求が処理中なら、GUIの `release` と同様に要求者へ移譲する。
- `release`（`force=True`）: 誰が保持していても（GUIのクライアント、`get` した主体とは別の外部プログラムを含む）無条件で即時解放し、ブロックも解除する。GUIの旧保持者には `operator_revoked`（「操作権が外部から強制解放されました」）を送り、処理中の強制取得要求は破棄して要求者に `takeover_result: rejected` を送る。空いているときは何もせず成功する。
- REST: `POST /api/priority/get`（body: `{"block": bool, "force": bool, "name": str}`、応答にトークン）、`POST /api/priority/release`（body: `{"token": str, "force": bool}`）。設定 `priority_hosts`（既定はループバックのみ）に含まれる接続元以外からは `403`。
- 外部プログラムが実機に直接アクセスする場合でも、devgui側のDisplayのポーリングや接続時の `get` は止まらない（操作権はGUI利用者の操作を止めるだけ）。

---

## 10. エラー処理とログ

- 部品の `call`、`open`、`close` が例外を投げてもサーバーは落ちない。
- 操作系の例外: 呼び出したクライアントにエラーを返し、画面上でトースト表示＋該当パネルに直近のエラーを表示する。
- ポーリングの例外: Displayのログにエラー行として記録する（デバイス状態は変えない）。
- `open` の例外: デバイスを `error` 状態にし、メッセージをパネルに表示する。
- サーバーのログはPython標準の `logging` で標準出力に出す（systemd経由でjournaldに記録される）。操作系の呼び出し（誰が、どの部品を、どの値で、結果）はINFOで記録する。
- 例外のトレースバックはサーバーログにのみ出し、画面には例外の型とメッセージのみを表示する。

---

## 11. 画面構成（フロントエンド）

- 上部バー: アプリ名、操作権の状態（保持者名／自分が保持中）、「操作権を取得」「操作権を要求」「操作権を解放」ボタン（状態に応じて出し分け）、WebSocket接続状態。
- タブ列: `Category` ごと。
- タブ内: `Device` ごとのパネルをグリッド状（画面幅に応じて列数が変わるレスポンシブ）に並べる。
- パネルヘッダー: デバイス名、状態表示（色付きバッジ）、接続・切断ボタン（open/closeがある場合）。`not_installed` はグレーのバッジ（例:「未実装」）で表示し、接続・切断ボタンは出さない。
- パネル本体: 部品を定義順に縦に並べる。
- `Toggle` は左右にスライドしてon/offを選ぶスイッチの外観で表示する（チェックボックスではない）。「OFF スイッチ ON」の順でラベルを添え、現在選ばれている側の文字を強調する。
- ダイアログ: 強制取得要求（保持者側、カウントダウン付き）、待機表示（要求者側、カウントダウン付き）、`confirm=True` のボタン確認。
- エラー表示欄（パネル単位・部品単位）は、エラーがないときも固定の1行分の高さで確保しておき、エラーの表示・消去でパネルの大きさが変わらないようにする。1行に収まらないメッセージは末尾を「…」で省略し、全文はマウスオーバーで表示する（トーストとコマンドログにも全文が出る）。
- 画面最下部: 操作コマンドログ（8.2）の固定表示エリア。タブ切り替えの影響を受けない。
- WebSocket切断時は画面上に明示し、自動で再接続を試みる（指数バックオフ）。再接続後は `snapshot` で状態を復元する。

---

## 12. 設定値

`layout.py` 内の `settings = Settings(...)` またはCLI引数で変更可能。CLI引数が優先。

| 名前 | 既定値 | 説明 |
|---|---|---|
| `host` | `0.0.0.0` | 待ち受けアドレス |
| `port` | `8000` | 待ち受けポート |
| `title` | `"devgui"` | 画面上部のアプリ名 |
| `operator_idle_timeout` | 600秒 | 無操作による操作権の自動解放 |
| `operator_disconnect_grace` | 30秒 | 切断後の猶予 |
| `takeover_wait` | 10秒 | 強制取得の応答待ち時間 |
| `takeover_cooldown` | 30秒 | 拒否後の再要求禁止時間 |
| `slow_call_warning` | 30秒 | 長時間呼び出しの警告閾値 |
| `priority_hosts` | `("127.0.0.1", "::1")` | 外部取得API（9.5）を許可する接続元 |

---

## 13. デプロイ

- `deploy/devgui.service` にsystemdユニットの例を用意する（`Restart=on-failure`、実行ユーザー指定、`WorkingDirectory`、venvのPythonを使う `ExecStart`）。
- GPIBデバイスファイル等へのアクセス権のため、実行ユーザーが必要なグループ（例: `gpib`、`dialout`）に属している必要がある旨をREADMEに記載する。
- 停止時（SIGTERM）に6.6の `close()` 処理が走ることを確認する。

---

## 14. 実装順序

各ステップで動作確認とテストを行ってから次に進む。デバイスは `examples/mock_devices.py` のダミーで開発する。

1. **部品とレイアウトのモデル**: `widgets.py`、`layout_loader.py`、検証とエラーメッセージ。
2. **ダミーデバイス**: open/close/set/get を持ち、遅延・ランダム例外・open失敗を設定できるもの。open を持たないもの、親クラスにだけ open があるものも用意する。
3. **バスワーカー**: 直列化、優先度、asyncio連携。
4. **デバイス管理**: 状態遷移、自動open、手動open/close、終了時close。
5. **FastAPIとWebSocketの骨格**: `/api/layout`、`/api/call`、`hello` / `snapshot`。操作権チェックはこの時点では仮で通す。
6. **フロントエンドの基本**: タブ、パネル、全部品の描画と呼び出し。
7. **Displayのログ**: ポーラー、リングバッファ、画面表示（スクロール保持を含む）。
8. **操作権**: 取得・解放・自動解放、トークン検証、UIの出し分け。
9. **強制取得**: 状態機械、ダイアログ、クールダウン。
10. **仕上げ**: エラー表示、再接続、CLI、systemdユニット、README。

---

## 15. テスト方針

- **単体テスト（pytest）**
  - レイアウト検証（重複名、不正な値など）。
  - バスワーカー: 同一バスで呼び出しが重ならないこと、異なるバスは並行に動くこと、ユーザー操作がポーリングより優先されること、ポーリングのスキップ。
  - デバイス状態遷移: open失敗、openなしデバイス、終了時close。
  - 操作権の状態機械: 取得・解放・無操作タイムアウト・切断猶予、強制取得（許可／拒否／無応答／取り消し／要求者切断／保持者の自発的解放）、クールダウン、移譲後の旧トークン拒否。注入クロックで時間を進めて検証する。
  - Displayのリングバッファ上限と例外行。
- **結合テスト**
  - FastAPIの `TestClient` でREST・WebSocketの流れを検証する（2クライアントでの強制取得を含む）。
- **手動確認**
  - `examples/layout_example.py` で起動し、2つのブラウザから操作権のやり取りと表示を確認する手順をREADMEに記載する。

---

## 16. 未決定事項・将来の拡張

- `Display` の新しい行を上に積むか下に積むか（現状: 上）。
- ログのCSV保存やグラフ表示（現状: 対象外）。
- 操作履歴の画面表示（現状: サーバーログのみ）。
- `Toggle` / `Select` の実機状態の読み戻し（現状: なし）。
- `layout.py` の変更をサーバー再起動なしで反映する機能（現状: 再起動が必要）。
