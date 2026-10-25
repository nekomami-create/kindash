# ごみの日に Kindle が鳴らす音声を、Windows の日本語音声（Haruka）で作る。
# Kindle 3 の読み上げは英語しか話せないので、決まった文を前もって WAV にしておく。
#
#   powershell -ExecutionPolicy Bypass -File tools\make_voices.ps1
#
# cube\kindash_config.json の "garbage" を読み、kindle\voice\<音声の名前>.wav と chime.wav を作る。
# Microsoft の音声で作ったファイルなので、リポジトリには入れない（.gitignore）。
# このファイルは UTF-8 BOM 付きで保存する（BOM が無いと PowerShell 5.1 が日本語を壊す）。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'cube\kindash_config.json') | ConvertFrom-Json
$outDir = Join-Path $root 'kindle\voice'
New-Item -ItemType Directory -Force $outDir | Out-Null

$rate = 22050

function Write-Wav([string]$path, [int16[]]$samples) {
    $data = New-Object byte[] ($samples.Length * 2)
    [Buffer]::BlockCopy($samples, 0, $data, 0, $data.Length)
    $fs = [IO.File]::Create($path)
    $w = New-Object IO.BinaryWriter($fs)
    $w.Write([Text.Encoding]::ASCII.GetBytes('RIFF')); $w.Write([int](36 + $data.Length))
    $w.Write([Text.Encoding]::ASCII.GetBytes('WAVEfmt ')); $w.Write([int]16)
    $w.Write([int16]1); $w.Write([int16]1); $w.Write([int]$rate); $w.Write([int]($rate * 2))
    $w.Write([int16]2); $w.Write([int16]16)
    $w.Write([Text.Encoding]::ASCII.GetBytes('data')); $w.Write([int]$data.Length); $w.Write($data)
    $w.Close()
}

# チャイム（ピン・ポーン）。2つの音を指数的に減衰させる
$tones = @(@(659.25, 0.45), @(523.25, 0.9))
$list = New-Object 'System.Collections.Generic.List[int16]'
foreach ($t in $tones) {
    $n = [int]($rate * $t[1])
    for ($i = 0; $i -lt $n; $i++) {
        $env = [Math]::Exp(-4.0 * $i / $n)
        $v = [Math]::Sin(2 * [Math]::PI * $t[0] * $i / $rate) * 0.5 + [Math]::Sin(4 * [Math]::PI * $t[0] * $i / $rate) * 0.15
        $list.Add([int16](12000 * $env * $v))
    }
}
Write-Wav (Join-Path $outDir 'chime.wav') $list.ToArray()

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice('Microsoft Haruka Desktop')
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo($rate,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($g in $config.garbage) {
    $name, $voice = $g[0], $g[2]
    $synth.SetOutputToWaveFile((Join-Path $outDir "$voice.wav"), $fmt)
    $synth.Speak("今日は、${name}の日です。朝8時までに出しましょう。")
}
$synth.SetOutputToNull()
$synth.Dispose()
Get-ChildItem $outDir | Select-Object Name, Length | Format-Table -AutoSize
