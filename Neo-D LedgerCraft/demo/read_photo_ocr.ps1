$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $engineType = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $language = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]::new('en-GB')
    $engine = $engineType::TryCreateFromLanguage($language)
    if ($null -eq $engine) { exit 1 }

    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Wait-OcrOperation($operation, $type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
        $task.Wait()
        return $task.Result
    }

    $inputStream = [IO.MemoryStream]::new()
    [Console]::OpenStandardInput().CopyTo($inputStream)
    if ($inputStream.Length -lt 8 -or $inputStream.Length -gt 2MB) { exit 1 }
    $firstByte = $inputStream.GetBuffer()[0]
    $inputStream.Position = 0
    $imageStream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($inputStream)
    $decoderType = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
    $codec = if ($firstByte -eq 0xff) { $decoderType::JpegDecoderId } else { $decoderType::PngDecoderId }
    $decoder = Wait-OcrOperation ($decoderType::CreateAsync($codec, $imageStream)) $decoderType
    $maxSide = [Math]::Min(4096, $engineType::MaxImageDimension)
    if ($decoder.PixelWidth -lt 1 -or $decoder.PixelHeight -lt 1 -or
        $decoder.PixelWidth -gt $maxSide -or $decoder.PixelHeight -gt $maxSide -or
        ([long]$decoder.PixelWidth * [long]$decoder.PixelHeight) -gt 16000000) { exit 1 }

    $bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
    $format = [Windows.Graphics.Imaging.BitmapPixelFormat,Windows.Foundation,ContentType=WindowsRuntime]::Bgra8
    $alpha = [Windows.Graphics.Imaging.BitmapAlphaMode,Windows.Foundation,ContentType=WindowsRuntime]::Ignore
    $transform = [Windows.Graphics.Imaging.BitmapTransform,Windows.Foundation,ContentType=WindowsRuntime]::new()
    $orientation = [Windows.Graphics.Imaging.ExifOrientationMode,Windows.Foundation,ContentType=WindowsRuntime]::RespectExifOrientation
    $color = [Windows.Graphics.Imaging.ColorManagementMode,Windows.Foundation,ContentType=WindowsRuntime]::DoNotColorManage
    $bitmap = Wait-OcrOperation ($decoder.GetSoftwareBitmapAsync($format, $alpha, $transform, $orientation, $color)) $bitmapType
    $resultType = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
    $result = Wait-OcrOperation ($engine.RecognizeAsync($bitmap)) $resultType
    # OCR may return a separate line for a receipt's amount column. Rejoin only
    # fragments that share a visual row, then read them from left to right.
    $fragments = foreach ($line in $result.Lines) {
        if (-not $line.Words.Count) { continue }
        $left = [double]::PositiveInfinity
        $top = [double]::PositiveInfinity
        $bottom = [double]::NegativeInfinity
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            $left = [Math]::Min($left, $rect.X)
            $top = [Math]::Min($top, $rect.Y)
            $bottom = [Math]::Max($bottom, $rect.Y + $rect.Height)
        }
        if ($bottom -gt $top) {
            [pscustomobject]@{
                Left = $left
                Center = ($top + $bottom) / 2
                Height = $bottom - $top
                Text = $line.Text
            }
        }
    }

    $rows = [Collections.Generic.List[object]]::new()
    $row = $null
    foreach ($fragment in ($fragments | Sort-Object Center, Left)) {
        $tolerance = if ($null -eq $row) { 0 } else {
            [Math]::Max(3, [Math]::Min($row.Height, $fragment.Height) * 0.55)
        }
        if ($null -eq $row -or [Math]::Abs($fragment.Center - $row.Center) -gt $tolerance) {
            $row = [pscustomobject]@{
                Center = $fragment.Center
                Height = $fragment.Height
                Fragments = [Collections.Generic.List[object]]::new()
            }
            $rows.Add($row)
        }
        $row.Fragments.Add($fragment)
    }
    $lines = $rows | ForEach-Object {
        ($_.Fragments | Sort-Object Left | ForEach-Object Text) -join ' '
    }
    $text = $lines -join "`n"
    if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 16000) { exit 1 }
    [Console]::Write($text)
} catch {
    exit 1
}
