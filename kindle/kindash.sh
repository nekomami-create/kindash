#!/bin/sh
#
# kindash: Cube J1 が描いた画面を15分ごとに取ってきて、Kindle 3 の全画面に出す。
#
#   kindash.sh start    ダッシュボードにする（Kindle の標準画面を止める）
#   kindash.sh stop     普段の Kindle に戻す
#   kindash.sh status   動いているか
#   kindash.sh say      ごみの日の声を今すぐ試す（今日の分。収集日でなければ鳴らない）
#
# 動いている間のキー操作: HOME で普段の Kindle に戻る、それ以外のキーで今すぐ更新。
# Kindle 3 の busybox は 1.7 と古いので、grep -E や date -d は使わない。

INTERVAL=900            # 15分
OFFSET=120              # Cube は区切り+60秒で描くので、その1分後に取る
FULL_EVERY=4            # 4回に1回（1時間ごと）は全面を点滅させて残像を消す
KEY_HOME=102

DIR=/mnt/us/kindash
# Cube の URL などは kindash.conf に書く（リポジトリには入れない）:
#   URL="http://<CubeのIP>:8090/screen.png"
#   ANNOUNCE="07:30"     ごみの日を声で知らせる時刻（空なら知らせない）
#   VOLUME=100           そのときのスピーカーの音量（0〜127）
URL=""
ANNOUNCE=""
VOLUME=100
[ -f $DIR/kindash.conf ] && . $DIR/kindash.conf
GARBAGE_URL="${URL%/screen.png}/garbage"
VOICE=$DIR/voice
ANNOUNCED=$DIR/.announced
LOG=$DIR/kindash.log
PIDFILE=/var/run/kindash.pid
SLEEPFILE=/var/run/kindash.sleep
IMG=/tmp/kindash.png
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

log() {
    # Kindle の時計の表示はずれているが、unix 時間は正しいので日本時間に直して書く
    t=$(( $(date +%s) + 32400 ))
    printf '%02d:%02d:%02d %s\n' $(( t / 3600 % 24 )) $(( t / 60 % 60 )) $(( t % 60 )) "$*" >> $LOG
}

jst_hhmm() {
    t=$(( $(date +%s) + 32400 ))
    printf '%02d:%02d' $(( t / 3600 % 24 )) $(( t / 60 % 60 ))
}

ensure_wifi() {
    state=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)
    [ "$state" = "CONNECTED" ] && return 0
    log "wifi is $state, enabling"
    lipc-set-prop com.lab126.wifid enable 1 2>/dev/null
    n=0
    while [ $n -lt 60 ]; do
        sleep 2
        n=$(( n + 2 ))
        [ "$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)" = "CONNECTED" ] && return 0
    done
    return 1
}

get() {
    # get URL ファイル。busybox 1.7 の wget にはタイムアウトの指定がないので、自分で見張る
    rm -f "$2"
    wget -q -O "$2" "$1" &
    wpid=$!
    n=0
    while kill -0 $wpid 2>/dev/null; do
        sleep 1
        n=$(( n + 1 ))
        if [ $n -ge 60 ]; then
            kill $wpid 2>/dev/null
            log "fetch timed out: $1"
            return 1
        fi
    done
    wait $wpid
}

fetch() {
    get "$URL" $IMG.new || return 1
    [ -s $IMG.new ] || return 1
    mv $IMG.new $IMG
}

# --- ごみの日の声 ---
jst_day() {
    echo $(( ( $(date +%s) + 32400 ) / 86400 ))
}

announce_sec() {
    # "07:30" -> その日の 0時からの秒数（busybox 1.7 は 08 を8進数と読むので先頭の0を落とす）
    h=$(echo ${ANNOUNCE%%:*} | sed 's/^0\([0-9]\)/\1/')
    m=$(echo ${ANNOUNCE##*:} | sed 's/^0\([0-9]\)/\1/')
    echo $(( h * 3600 + m * 60 ))
}

announce() {
    [ "$(cat $ANNOUNCED 2>/dev/null)" = "$(jst_day)" ] && return 0
    ensure_wifi || return 1
    get "$GARBAGE_URL" /tmp/kindash.garbage || { log "garbage fetch failed"; return 1; }
    jst_day > $ANNOUNCED
    [ -s /tmp/kindash.garbage ] || { log "no garbage today"; return 0; }
    # 標準画面を止めているので音量は自分で上げ、鳴らし終えたら戻す
    old=$(amixer sget Speaker | sed -n 's/.*Front Left: Playback \([0-9]*\).*/\1/p')
    amixer -q sset Speaker $VOLUME,$VOLUME
    aplay -q $VOICE/chime.wav 2>/dev/null
    for v in $(cat /tmp/kindash.garbage); do
        if [ -f $VOICE/$v.wav ]; then
            aplay -q $VOICE/$v.wav 2>/dev/null
        else
            log "no voice for $v"
        fi
    done
    amixer -q sset Speaker ${old:-0},${old:-0}
    log "announced $(cat /tmp/kindash.garbage | tr '\n' ' ')"
}

announce_if_due() {
    # 時刻ちょうどに鳴らせなかったとき（再起動中など）のために、15分以内なら更新のついでに鳴らす
    [ -n "$ANNOUNCE" ] || return 0
    s=$(( ( $(date +%s) + 32400 ) % 86400 ))
    a=$(announce_sec)
    [ $s -ge $a ] && [ $s -lt $(( a + INTERVAL )) ] && announce
}

show_error() {
    # eips の文字は ASCII だけ。最下段の右端に小さく出す
    eips 32 39 "! fetch failed $(jst_hhmm)" >/dev/null 2>&1
}

update() {
    if ensure_wifi && fetch; then
        count=$(( count + 1 ))
        if [ $(( count % FULL_EVERY )) -eq 1 ]; then
            eips -f -g $IMG >/dev/null 2>&1
        else
            eips -g $IMG >/dev/null 2>&1
        fi
        log "updated ($count)"
    else
        log "update failed"
        show_error
    fi
}

sleep_until_next() {
    # 次の更新か、ごみの日を知らせる時刻の早い方まで寝る。どちらで起きたかを WAKE に入れる
    now=$(date +%s)
    next=$(( now - now % INTERVAL + INTERVAL + OFFSET ))
    [ $(( next - now )) -gt $INTERVAL ] && next=$(( next - INTERVAL ))
    WAKE=update
    if [ -n "$ANNOUNCE" ]; then
        s=$(( ( now + 32400 ) % 86400 ))
        ann=$(( now - s + $(announce_sec) ))
        [ $ann -le $now ] && ann=$(( ann + 86400 ))
        if [ $ann -lt $next ]; then
            next=$ann
            WAKE=announce
        fi
    fi
    sleep $(( next - now )) &
    echo $! > $SLEEPFILE
    wait $!
    rm -f $SLEEPFILE
    # キーで早く起こされたら更新する
    [ $(date +%s) -lt $(( next - 5 )) ] && WAKE=update
}

watch_keys() {
    while true; do
        key=$(waitforkey 2>/dev/null)
        code=${key%% *}
        [ -z "$code" ] && { sleep 1; continue; }
        if [ "$code" = "$KEY_HOME" ]; then
            log "HOME pressed, leaving dashboard"
            sh "$0" stop &
            return
        fi
        # 寝ているところを起こして、すぐ更新させる
        [ -f $SLEEPFILE ] && kill $(cat $SLEEPFILE) 2>/dev/null
        sleep 2
    done
}

run() {
    trap "" HUP
    echo $$ > $PIDFILE
    log "start"
    lipc-set-prop com.lab126.powerd preventScreenSaver 1
    /etc/init.d/framework stop >/dev/null 2>&1
    sleep 2
    eips -c >/dev/null 2>&1
    count=0
    watch_keys &
    echo $! > $PIDFILE.keys
    update
    announce_if_due
    while true; do
        sleep_until_next
        if [ "$WAKE" = announce ]; then
            announce
        else
            update
            announce_if_due
        fi
    done
}

stop() {
    if [ -f $PIDFILE.keys ]; then
        kill $(cat $PIDFILE.keys) 2>/dev/null
        rm -f $PIDFILE.keys
    fi
    if [ -f $PIDFILE ]; then
        kill $(cat $PIDFILE) 2>/dev/null
        rm -f $PIDFILE
    fi
    [ -f $SLEEPFILE ] && kill $(cat $SLEEPFILE) 2>/dev/null
    rm -f $SLEEPFILE
    killall waitforkey 2>/dev/null
    lipc-set-prop com.lab126.powerd preventScreenSaver 0
    eips -c >/dev/null 2>&1
    /etc/init.d/framework start >/dev/null 2>&1
    log "stop"
}

case "$1" in
    start)
        if [ -f $PIDFILE ] && kill -0 $(cat $PIDFILE) 2>/dev/null; then
            echo "already running"
            exit 0
        fi
        if [ -z "$URL" ]; then
            echo "set URL in $DIR/kindash.conf first"
            exit 1
        fi
        # busybox 1.7 には nohup がないので、run 側で HUP を無視して裏に回す
        ( sh "$0" run </dev/null >/dev/null 2>&1 & )
        echo "started"
        ;;
    run)
        run
        ;;
    stop)
        stop
        echo "stopped"
        ;;
    say)
        rm -f $ANNOUNCED
        announce
        ;;
    status)
        if [ -f $PIDFILE ] && kill -0 $(cat $PIDFILE) 2>/dev/null; then
            echo "running (pid $(cat $PIDFILE))"
            tail -n 5 $LOG
        else
            echo "not running"
        fi
        ;;
    *)
        echo "usage: $0 start|stop|status|say"
        exit 1
        ;;
esac
