# devgui

Ubuntuサーバーに接続された複数の計測器・デバイスを、LAN内のブラウザから操作・監視するWebアプリケーション。

既存のPythonデバイス制御ライブラリをコード変更なしにWeb GUIから操作でき、GUIの構成は1つの `layout.py` で宣言する。詳細な設計は `docs/design.md` を参照。サーバの起動・停止と操作権（外部プログラムからの取得・ブロックを含む）の運用手順は `docs/manual.md` を参照。

## 動作要件

- Python 3.10以上
- Ubuntu等Linux（`socket.gethostbyaddr` によるクライアント逆引き、systemdでの常駐運用を想定）

## インストール

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## layout.py の書き方

```python
from devgui import Category, Device, Button, NumberInput, Display, Settings
from mylib import PowerSupply

psu1 = PowerSupply("GPIB0::5::INSTR")

layout = [
    Category("電源", [
        Device("PSU1", psu1, bus="gpib0", widgets=[
            NumberInput("電圧設定", call=psu1.set, unit="V", min=0, max=30, step=0.001),
            Display("出力電圧", call=psu1.get, unit="V", poll=1.0, fmt="{:.3f}"),
        ]),
    ]),
]

# 任意。省略時は既定値、CLI引数が指定されればさらにそちらが優先。
settings = Settings(host="0.0.0.0", port=8000, title="My Lab")
```

構成要素・部品の全リスト、操作権・強制取得の仕様などは `docs/design.md` を参照。

## 起動

```bash
python -m devgui --layout /path/to/layout.py --host 0.0.0.0 --port 8000
```

`--host`/`--port` を省略した場合は `layout.py` の `settings`（未指定なら既定値 `0.0.0.0:8000`）を使う。ブラウザで `http://<サーバー>:<port>/` を開く。

`--dummy`（または環境変数 `DEVGUI_DUMMY=1`）でダミーモード（実機に接続しない）になる。`layout.py` 側は `devgui.is_dummy()` で判定する（例: `examples/layout_example.py`）。

## GPIB等ハードウェアへのアクセス権

デバイスファイル（`/dev/ttyUSB*`、Prologix GPIB-Ethernetアダプタ等）にアクセスするデバイス制御ライブラリを使う場合、devguiを実行するユーザーがそのデバイスファイルの属するグループ（環境により `dialout`、`gpib` など）に所属している必要がある。

```bash
sudo usermod -aG dialout devgui   # 例
```

## systemdでの常駐化

`deploy/devgui.service` を `/etc/systemd/system/` に配置し、`WorkingDirectory`・`ExecStart`・（必要なら）`Environment=` をデプロイ環境に合わせて編集する。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now devgui
```

停止（`systemctl stop devgui` やサーバー再起動時のSIGTERM）では、接続中の全デバイスの `close()` が呼ばれてからプロセスが終了する。

## テスト

```bash
pytest
```

## 手動確認（2ブラウザでの操作権のやり取り）

1. `examples/mock_devices.py` のダミーデバイスを使った layout.py（本リポジトリのテストの `tests/fixtures/` や、自分で用意したもの）で `python -m devgui --layout ...` を起動する。
2. 2つの異なるブラウザ（またはシークレットウィンドウ）で同じURLを開く。
3. 片方で「操作権を取得」→ 自分側に「(自分)」表示、もう片方は保持者名のみ表示され操作系が無効化されていることを確認する。
4. もう片方で「操作権を要求」→ 保持者側にカウントダウン付きダイアログが出ることを確認する。「許可」すると要求側に操作権が移り、両画面の表示が即座に更新されることを確認する。
5. 再度要求し、今度は「拒否」する。要求側にトースト通知が出て、しばらく（既定30秒）は再要求できない（429）ことを確認する。
6. 保持側でNumberInput等を操作し、Displayのログが両画面（閲覧は誰でも可能）にリアルタイムで反映されることを確認する。
