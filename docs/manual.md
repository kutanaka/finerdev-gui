# devgui 運用マニュアル（サーバの起動・停止と操作権）

日常の運用で必要な手順をまとめたもの。仕様の詳細は `docs/design.md`（操作権は9章）を参照。

以下、リポジトリを `~/finer/finerdev-gui`、その中の venv を `.venv` とする。

---

## 1. 初回セットアップ

### 1.1 venv の作成

finerdev はシステムの Python（`~/.local`）にインストールされているため、それを参照できるよう `--system-site-packages` 付きで venv を作る。

```bash
cd ~/finer/finerdev-gui
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
```

確認:

```bash
FINER_LOGDIR=/tmp .venv/bin/python -c 'import uvicorn, fastapi, finerdev; print("ok")'
```

### 1.2 FINER_LOGDIR

finerdev は import 時に環境変数 `FINER_LOGDIR`（ログの置き場）を読む。`examples/layout_example.py` は、

- 環境変数 `FINER_LOGDIR` が設定されていれば**そちらを優先**して使う。
- 設定されていなければ `~/finer/log`（起動ユーザーのホーム）を使う。

別の場所を使う場合だけ設定すればよい:

```bash
export FINER_LOGDIR=/path/to/log
```

`~/finer/log` を使う場合は、そのディレクトリが存在していること（実機モードの loatt 等はここにログを書く）。

### 1.3 finerdev を更新したとき

finerdev（`~/finer/devices`）を変更した場合は、システムの Python に入れ直す。

```bash
cd ~/finer/devices
pip install --user --no-deps --no-build-isolation .
```

---

## 2. サーバの起動

### 2.1 実機モードとダミーモード

レイアウトは `examples/layout_example.py` の1つで、起動時にモードを選ぶ。

| モード | 指定方法 | 動作 |
|---|---|---|
| 実機モード | （既定） | 実機に接続して操作する |
| ダミーモード | `--dummy` オプション、または環境変数 `DEVGUI_DUMMY=1` | 実機に一切接続しない。finerdev の本物のコンストラクタで構築し、通信部分だけダミーに置き換える（`examples/finer_dummy.py`。sweep は約10秒かかる実機の動作を模擬） |

ダミーモードでは、画面のタイトルに「[ダミー]」が付き、上部バーが茶色の縞模様になる。モードは起動時に決まり、切り替えるにはサーバを再起動する（3章 → 2章）。ダミーモードの詳しい動作は 2.6 を参照。

### 2.2 前面で起動（開発・確認向け）

```bash
cd ~/finer/finerdev-gui
.venv/bin/python -m devgui --layout examples/layout_example.py --host 0.0.0.0 --port 8000           # 実機モード
.venv/bin/python -m devgui --layout examples/layout_example.py --host 0.0.0.0 --port 8000 --dummy   # ダミーモード
```

- `--host 0.0.0.0`: LAN 内の他の PC から接続できる。自分のマシンからだけ使う場合は `--host localhost`。
- `--host`/`--port` を省略すると、layout.py の `settings`（なければ `0.0.0.0:8000`）を使う。
- ブラウザで `http://<サーバ名またはIP>:8000/` を開く（同じマシンなら `http://localhost:8000/`）。

### 2.3 バックグラウンドで起動

ターミナルを閉じても動き続けるようにする場合:

```bash
cd ~/finer/finerdev-gui
nohup .venv/bin/python -m devgui \
    --layout examples/layout_example.py --host 0.0.0.0 --port 8000 \
    > ~/devgui.log 2>&1 &
```

ダミーモードなら2行目の末尾に `--dummy` を加える（`--port 8000 --dummy \`）。

ログは `~/devgui.log` に出る（`tail -f ~/devgui.log` で追える）。

### 2.4 systemd で常駐（本番運用）

`deploy/devgui.service` を `/etc/systemd/system/` に置き、`User`・`WorkingDirectory`・`ExecStart` を環境に合わせて編集する。必要に応じて `[Service]` に次の行を追加する。

```ini
# FINER_LOGDIR を ~/finer/log（User のホーム）以外にする場合
Environment=FINER_LOGDIR=/path/to/log
# ダミーモードで常駐させる場合（ExecStart に --dummy を付けても同じ）
Environment=DEVGUI_DUMMY=1
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now devgui     # 起動＋OS起動時の自動起動
journalctl -u devgui -f                # ログ
```

### 2.5 起動の確認

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/    # 200 なら起動済み
ss -ltnp | grep 8000                                               # 待ち受けプロセス
```

起動直後のログに各デバイスの `open()` の結果が出る。画面では各パネルのバッジが「接続済み」になっていれば正常。

### 2.6 ダミーモードの詳細

#### 用途

実機をつながずに（または実機に影響を与えずに）GUI を動かすためのモード。

- GUI の見た目・操作の確認、改修後の動作確認
- 操作権のやり取り（要求・強制取得・ブロック、外部プログラムからの操作）の練習や確認
- sweep の実行中表示、I-V グラフ、PDF 出力の確認

#### 仕組み

- デバイスのインスタンスは、実機モードと同じく **finerdev の本物のコンストラクタ**に、**実機と同じ引数**（`docs/device_list.txt` のアドレス等）を渡して作る。クラス名や引数の誤りは実機モードと同じように起動時のエラーになる。
- 機器と通信するメソッドだけを、メモリ上で値を保持するダミーに置き換える（`examples/finer_dummy.py`）。ネットワーク・GPIB には一切接続しない。
- ダミーは置き換え先の finerdev のメソッドと名前・引数が完全に一致していることを起動時に検査する。finerdev 側でメソッド名や引数が変わった場合は、起動時にエラーになって気付ける。
- GUI からの呼び出しとコマンドログの表示（`SourceMeter1.setV(0.002) -> True` など）は実機モードと同じ。

#### 各デバイスのダミーの動作

| デバイス | ダミーの動作 |
|---|---|
| SourceMeter1〜4 | 設定電圧と出力 ON/OFF をメモリに保持する。meas は設定電圧に対して、ダイオード風の I-V 曲線（0〜5 mV で 0〜約 0.03 mA）に小さなノイズを加えた電流を返す。sweep は同じ曲線を指定範囲で返し、実機に合わせて**点数 × 0.2 秒（最大10秒）かかる**（既定の 0〜5 mV / 0.1 mV 刻みで約10秒） |
| LO att1, att2 | 設定値をメモリに保持し、読み出すとその値を返す（起動時は 0） |
| synth (B4+5), (B6+7) | 周波数・出力レベル・出力 ON/OFF をメモリに保持し、読み出すとその値を返す（起動時は 0） |
| multiplier | on/off をメモリに保持し、状態読み出しで `(True, '1')` / `(True, '0')` を返す |
| 全デバイス共通 | 接続（open）・切断（close）は常に成功する |

#### 実機モードとの違い・注意

- 値はサーバのプロセス内にしかない。サーバを再起動すると初期値に戻る。
- 通信エラーや機器側のエラーは発生しない（ネットワーク断・タイムアウト等の確認には使えない）。
- finerdev のメソッド内にある範囲チェック（例: SourceMeter の setV の 5 mV 上限）はダミーに置き換わるため働かない。devgui 側の範囲チェック（入力欄の min/max。SourceMeter は 0〜5 mV）は実機モードと同じく働く。
- SourceMeter の `get()` の値の並び（[電流, 電圧, ...]）は devgui の仕様どおりに作ってある。**実機の finerdev が返す並びの確認にはならない**。
- 起動ログに `opening gpib dev25 on prologix` のような行が出るが、finerdev のコンストラクタ内の表示だけで、実際には接続しない。
- ダミーは `FINER_LOGDIR` にログを書かない（実機モードの loatt 等は書く）。

#### ダミーモードかどうかの確認

- 画面: タイトルが「… [ダミー]」、上部バーが茶色の縞模様。
- サーバのログ: 起動時に `WARNING devgui: DUMMY mode: layout.py is asked not to touch hardware`。
- コマンドから: `curl -s http://localhost:8000/api/layout | grep -o '"dummy":[a-z]*'` が `"dummy":true`。

#### 実機モードと同時に動かす

ポートを変えれば、実機モードのサーバとは別にダミーモードのサーバを同じマシンで起動できる（プロセスが別なので互いに影響しない。操作権もサーバごとに別）。

```bash
.venv/bin/python -m devgui --layout examples/layout_example.py --host 0.0.0.0 --port 8001 --dummy
```

外部プログラムからの操作権（5章）を試す場合は、`--url http://localhost:8001`（Python では `url=` 引数）でダミー側を指定する。

---

## 3. サーバの停止

停止時には、接続中の全デバイスの `close()` が呼ばれてから終了する（そのため数秒かかることがある）。

| 起動方法 | 停止方法 |
|---|---|
| 前面で起動 | そのターミナルで `Ctrl+C` |
| バックグラウンド（nohup） | `pkill -f "python -m devgui --layout"` |
| systemd | `sudo systemctl stop devgui` |

プロセスを確認してから止める場合:

```bash
pgrep -af "python -m devgui"      # PID とコマンドラインを表示
kill <PID>                        # SIGTERM（正常終了。close() が呼ばれる）
```

`kill -9` は `close()` が呼ばれないため、応答しない場合の最終手段にする。

再起動は「停止 → 起動」。systemd なら `sudo systemctl restart devgui`。

---

## 4. 操作権（GUI）

### 4.1 基本

- 操作権はサーバ全体で1つ。持っているブラウザだけがデバイスを操作できる（ボタン・入力・接続/切断）。
- 閲覧（タブ切り替え、測定値・ログの表示）は誰でもできる。
- 画面右上に現在の保持者が表示される。

| 右上の表示 | 意味 |
|---|---|
| `操作権: 空き` | 誰も持っていない |
| `操作権: <名前> (自分)` | このブラウザが保持中 |
| `操作権: <名前>` | 他のブラウザが保持中 |
| `操作権: <名前> (外部)` | 外部プログラムが保持中（5章） |
| `操作権: <名前> (外部) [ブロック中]` | 外部プログラムが保持し、GUIからの取得・要求を拒否中 |

### 4.2 取得と解放

- **取得**: 空いていれば「操作権を取得」を押す。
- **解放**: 「操作権を解放」を押す。
- 同じタブでリロードしても、30秒以内に再接続すれば操作権は保持されたまま。

### 4.3 他の人から譲ってもらう（要求）

1. 「操作権を要求」を押す。自分の画面に待機ダイアログと残り秒数（既定10秒）が表示される。
2. 保持者の画面にカウントダウン付きの確認ダイアログが出る。
   - **許可** → すぐに要求者へ移る。
   - **拒否** → 要求者に通知される。要求者は30秒間は再要求できない。
   - **応答なし**（10秒経過、保持者が席を外している・ブラウザを閉じている場合を含む）→ 要求者へ移る。
3. 待機中に「取り消し」を押せば要求を撤回できる。

### 4.4 自動解放

| 条件 | 既定値 |
|---|---|
| 最後の操作から無操作が続いた | 10分 |
| 保持者のブラウザが切断され、再接続がない | 30秒 |

外部プログラムによる保持（5章）には自動解放はない。

---

## 5. 操作権（外部プログラムから）

測定スクリプトの実行中など、GUI の利用者に機器を触らせたくないときに使う。

### 5.1 使える場所

- **devgui サーバと同じマシン上でのみ実行できる**（他のPCからは `403` で拒否される。許可する接続元は `Settings(priority_hosts=...)` で変更可能）。
- devgui を import できる Python が必要。venv の python を使うのが簡単:

```bash
~/finer/finerdev-gui/.venv/bin/python ...
```

システムの python から使う場合は次のどちらか:

```bash
PYTHONPATH=~/finer/finerdev-gui python ...            # その都度
pip install --user -e ~/finer/finerdev-gui           # 常に import できるようにする
```

### 5.2 Python から

```python
from devgui.priority import priority

priority("get", block=True, force=True, name="IV測定スクリプト")
try:
    ...  # 測定
finally:
    priority("release")
```

| 呼び出し | 動作 |
|---|---|
| `priority("get")` | 空いていれば取得。GUIのユーザーが保持中なら失敗（`PriorityError`） |
| `priority("get", force=True)` | 誰が持っていても無条件で即時取得。GUIの保持者には「強制取得されました」と通知される |
| `priority("get", block=True)` | 取得し、解放するまでGUIからの取得・要求をすべて即時却下する |
| `priority("release")` | 解放（ブロックも解除）。**`get` した本人（同じプロセス）のみ**有効 |
| `priority("release", force=True)` | 誰が持っていても無条件で即時解放（ブロックも解除）。`get` した主体と違っても、GUIの保持者に対しても有効 |

- `name` は GUI の右上に保持者名として表示される（省略時「外部プログラム」）。
- `block=False` で保持している場合、GUI から「操作権を要求」されると、応答する者がいないため10秒後にGUI側へ移る。
- 外部プログラムの保持は時間切れにならない。**必ず `release` する**（上の例のように `try/finally` で囲む）。
- `get` の戻り値の `token` を `priority("release", token=...)` に渡せば、別のプロセスからでも本人として解放できる。

### 5.3 シェルから

シェルのコマンドは毎回別プロセスなので、解放には `--force`（または `get` が表示したトークン）を使う。

```bash
PY=~/finer/finerdev-gui/.venv/bin/python

$PY -m devgui.priority get --force --block --name "手動ブロック"   # 取得＋ブロック（トークンを表示）
$PY -m devgui.priority release --force                             # 無条件で解放

T=$($PY -m devgui.priority get --block)                            # 本人として解放する場合
$PY -m devgui.priority release --token "$T"
```

Python なしで curl でも操作できる:

```bash
curl -X POST -H 'Content-Type: application/json' -d '{"force": true, "block": true, "name": "手動"}' http://localhost:8000/api/priority/get
curl -X POST -H 'Content-Type: application/json' -d '{"force": true}' http://localhost:8000/api/priority/release
```

### 5.4 注意

- 操作権が止めるのは GUI 利用者の操作だけ。devgui 自身の定期読み出し（Display のポーリング）や接続時の読み出しは止まらない。
- 外部プログラムが実機に直接接続する場合、devgui も同じ機器に接続したままである点に注意（機器によっては同時接続できない）。必要なら GUI で該当デバイスを「切断」してから使う。

---

## 6. トラブルシューティング

| 症状 | 原因と対処 |
|---|---|
| GUI で「外部プログラムが操作権をブロックしています」と出て何もできない | 外部から `block=True` で保持されたまま。スクリプトが異常終了した場合など。サーバのマシンで `…/.venv/bin/python -m devgui.priority release --force` |
| `devgui.priority: release: HTTP 403: not the external holder that got the right` | `get` した本人以外が `release` した。`--force` を付ける |
| `HTTP 403: priority API not allowed from …` | サーバと別のマシンから実行した。サーバのマシン上で実行する |
| `HTTP 409: operator right is held by a GUI client` | GUIの利用者が保持中に `force` なしで `get` した。`force=True` を付けるか、解放を待つ |
| `cannot reach devgui at http://localhost:8000` | サーバが起動していない、またはポートが違う（`--url http://localhost:<port>`） |
| `No module named devgui` | devgui を import できない Python で実行した。venv の python を使う（5.1） |
| `No module named uvicorn` | venv 以外の python でサーバを起動した。`.venv/bin/python -m devgui ...` で起動する |
| 起動時 `KeyError: 'FINER_LOGDIR'` | `layout_example.py` 以外の layout.py から finerdev を import している。環境変数 `FINER_LOGDIR` を設定する（1.2） |
| 実機につながっているはずなのに値が変わらない／タイトルに「[ダミー]」 | ダミーモードで起動している。`--dummy` を外し、環境変数 `DEVGUI_DUMMY` が設定されていないか確認して再起動する |
| 起動時 `cannot import name 'SourceMeter2400'` | インストール済みの finerdev が古い。1.3 の手順で入れ直す |
| 起動時 `address already in use` | 既にサーバが動いている（`pgrep -af "python -m devgui"`）か、別のプロセスがポートを使用中 |
| 別のPCのブラウザから開けない | `--host localhost` で起動している。`--host 0.0.0.0` で起動し直す |
| パネルのバッジが「エラー」 | そのデバイスの `open()` が失敗した（パネルにメッセージが出る）。機器の電源・ネットワークを確認し、操作権を取って「接続」を押す |
