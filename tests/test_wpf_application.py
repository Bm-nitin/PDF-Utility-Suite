import json
import sys

import pymupdf
import pytest
from PIL import Image

import app
import app_logging
import ui_theme
import wpf_app
import wpf_backend


def _make_pdf(path, texts):
    with pymupdf.open() as document:
        for text in texts:
            document.new_page().insert_text((72, 72), text)
        document.save(path)


_MAXIMIZE_CHECK = r"""
$window.ShowActivated = $false
$window.ShowInTaskbar = $false
$window.WindowStartupLocation = 'Manual'
$window.Left = -20000
$window.Top = -20000

function Toggle-WindowSize {
    $ToggleWindowSize.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
}

$qaState = @{ Step = 0; Start = [DateTime]::UtcNow }
$qa = [Windows.Threading.DispatcherTimer]::new()
$qa.Interval = [TimeSpan]::FromMilliseconds(40)
$qa.Add_Tick({
    if (([DateTime]::UtcNow - $qaState.Start).TotalSeconds -gt 10) {
        throw "Maximize test timed out at step $($qaState.Step) in state $($window.WindowState)."
    }
    if (-not $ui.Ready) { return }
    switch ($qaState.Step) {
        0 {
            $ui.Navigation.split.IsChecked = $true
            Toggle-WindowSize
            $qaState.Step = 1
        }
        1 {
            if ($window.WindowState -ne 'Maximized') { return }
            $window.UpdateLayout()
            $handle = [Windows.Interop.WindowInteropHelper]::new($window).Handle
            $workArea = [Windows.Forms.Screen]::FromHandle($handle).WorkingArea
            foreach ($control in @($RunAction, $SettingsButton)) {
                $bottomRight = $control.PointToScreen(
                    [Windows.Point]::new($control.ActualWidth, $control.ActualHeight)
                )
                if ($bottomRight.Y -gt $workArea.Bottom) {
                    throw "$($control.Name) bottom ($($bottomRight.Y)) exceeds the work area bottom ($($workArea.Bottom))."
                }
            }
            Toggle-WindowSize
            $qaState.Step = 2
        }
        2 {
            if ($window.WindowState -ne 'Normal') { return }
            $qa.Stop()
            $window.Close()
        }
    }
})
$qa.Start()
Start-Interface
"""


@pytest.mark.skipif(sys.platform != "win32", reason="The WPF interface requires Windows.")
def test_maximized_window_keeps_bottom_controls_above_taskbar(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    wpf_app.run(entry=_MAXIMIZE_CHECK)


_UI_CHECK = r"""
$window.ShowActivated = $false
$window.ShowInTaskbar = $false
$window.WindowStartupLocation = 'Manual'
$window.Left = -20000
$window.Top = -20000
$files = $env:PDF_WPF_TEST_FILES | ConvertFrom-Json

function Choose-SavePath([string]$suggestion) { return $files.output }

function Open-UpdateLink([string]$url) { $qaState.OpenedUrl = $url }

function QA-Import([string]$tool, $paths) {
    $ui.Navigation[$tool].IsChecked = $true
    $ui.Busy = $true
    Update-Controls
    Send-Request @{ action = 'import'; tool = $tool; paths = @($paths); existing = @() } $ui.Active
}

function QA-MoveFirstToLast {
    $window.UpdateLayout()
    $first = $FileList.ItemContainerGenerator.ContainerFromIndex(0)
    $neighbor = $FileList.ItemContainerGenerator.ContainerFromIndex(1)
    $source = $ui.Active.Rows[0]
    if (-not $first -or -not $neighbor) { throw 'The reorderable rows were not rendered.' }
    $press = $first.TranslatePoint([Windows.Point]::new(14, 24), $FileList)
    Start-RowDrag $first $press
    $target = $ui.Active.Rows.Count - 1
    Update-RowDrag ([Windows.Point]::new($press.X, $press.Y + $target * $drag.Step))
    if ($drag.Slot -ne $target -or $FloatingContent.Content -ne $source -or
        $ReorderOverlay.Visibility -ne 'Visible' -or $first.Opacity -ne 0 -or
        $drag.Offsets[$neighbor] -ne -$drag.Step -or $ui.Active.Rows[0] -ne $source) {
        throw 'The production drag did not display the floating row and shift its neighbor.'
    }
    End-RowDrag $true
    if ($ui.Active.Rows[$target] -ne $source -or $ReorderOverlay.Visibility -ne 'Collapsed') {
        throw 'The production drop did not commit the new order.'
    }
}

function Wait-ForPreview {
    return ($ui.Active.Rows.Count -ge 2 -and $null -ne $ui.Active.Rows[0].Thumbnail)
}

$qaState = @{ Step = 0; Start = [DateTime]::UtcNow }
$qa = [Windows.Threading.DispatcherTimer]::new()
$qa.Interval = [TimeSpan]::FromMilliseconds(40)
$qa.Add_Tick({
    if (([DateTime]::UtcNow - $qaState.Start).TotalSeconds -gt 25) {
        throw "WPF test timed out at step $($qaState.Step). $($StatusText.Text)"
    }
    if (-not $ui.Ready -or $ui.Busy) { return }
    switch ($qaState.Step) {
        0 {
            if ($ui.States.Count -ne 12 -or $ui.Active.Tool.id -ne 'merge_compress') {
                throw 'The application did not register all twelve tools.'
            }
            foreach ($id in $ui.States.Keys) {
                $ui.Navigation[$id].IsChecked = $true
                if ($ui.Active.Tool.id -ne $id -or $Options.Children.Count -lt 1) {
                    throw "Could not display $id."
                }
            }
            QA-Import 'merge_compress' $files.merge
            $qaState.Step = 1
        }
        1 {
            if ($ui.Active.Rows.Count -ne 3) { throw $StatusText.Text }
            QA-MoveFirstToLast
            Write-Option 'mode' 'merge'
            Update-Controls
            $RunAction.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            $qaState.Step = 2
        }
        2 {
            if (-not [IO.File]::Exists($files.output)) { throw $StatusText.Text }
            QA-Import 'organize_pages' @($files.output)
            $qaState.Step = 3
        }
        3 {
            if (-not (Wait-ForPreview)) { return }
            QA-MoveFirstToLast
            if ($ui.Active.Values.order -ne '2, 3, 1') { throw 'Dragging pages did not update the order field.' }
            QA-Import 'images_to_pdf' $files.images
            $qaState.Step = 4
        }
        4 {
            if (-not (Wait-ForPreview)) { return }
            QA-MoveFirstToLast
            QA-Import 'pdf_to_images' @($files.output)
            $qaState.Step = 5
        }
        5 {
            if (-not (Wait-ForPreview)) { return }
            if (Can-Reorder) { throw 'The PDF-to-images list should select pages.' }
            [void]$FileList.SelectedItems.Add($ui.Active.Rows[1])
            if ($ui.Active.Values.scope -ne 'selected' -or $ui.Active.Values.pages -ne '2') {
                throw 'The image-export page selection did not update.'
            }
            $SettingsButton.IsChecked = $true
            if ($Workspace.Visibility -ne 'Collapsed' -or $SettingsView.Visibility -ne 'Visible' -or
                $window.OwnedWindows.Count) { throw 'Settings did not stay inside the main window.' }
            if ($LogPath.Text -ne $files.log -or -not $LogPath.IsReadOnly -or -not $OpenLogs.IsEnabled) {
                throw "Settings did not show the actual log path. Value: $($LogPath.Text)"
            }
            $OpenLogs.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            $DarkTheme.IsChecked = $true
            if ($window.Resources['Canvas'].Color.ToString() -ne '#FF0E1522') { throw 'Dark theme did not apply.' }
            $CheckUpdates.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            $qaState.Step = 6
        }
        6 {
            if ($ui.Updating) { return }
            if ($UpdateStatus.Text -notlike '*2.0.0*' -or $UpdateAction.Visibility -ne 'Visible' -or
                $UpdateAction.Content -ne 'Download Update') {
                throw 'The available update did not show a download button.'
            }
            $UpdateAction.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            if ($qaState.OpenedUrl -ne $files.download) {
                throw 'Download Update did not open the direct EXE URL.'
            }
            $CheckUpdates.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            $qaState.Step = 7
        }
        7 {
            if ($ui.Updating) { return }
            if ($UpdateAction.Visibility -ne 'Visible' -or $UpdateAction.Content -ne 'Download Setup' -or
                $UpdateStatus.Text -notlike '*Close PDF Utility Suite before running Setup.*') {
                throw 'The installed update did not show a Setup download and close instruction.'
            }
            $UpdateAction.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            if ($qaState.OpenedUrl -ne $files.setup) {
                throw 'Download Setup did not open the direct installer URL.'
            }
            $CheckUpdates.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            $qaState.Step = 8
        }
        8 {
            if ($ui.Updating) { return }
            if ($UpdateAction.Visibility -ne 'Visible' -or $UpdateAction.Content -ne 'Open release page') {
                throw 'The update without an EXE did not show the release page button.'
            }
            $UpdateAction.RaiseEvent([Windows.RoutedEventArgs]::new([Windows.Controls.Button]::ClickEvent))
            if ($qaState.OpenedUrl -ne $files.release) {
                throw 'The fallback button did not open the release page.'
            }
            $ui.Navigation.merge_compress.IsChecked = $true
            if ($ui.Active.Rows.Count -ne 3 -or $ui.Active.Rows[2].Name -ne 'Résumé.pdf') {
                throw 'Settings navigation lost the imported file order.'
            }
            $qa.Stop()
            $window.Close()
        }
    }
})
$qa.Start()
Start-Interface
"""


@pytest.mark.skipif(sys.platform != "win32", reason="The WPF interface requires Windows.")
def test_wpf_window_bridge_and_animated_tool_lists(tmp_path, monkeypatch, isolated_logging):
    names = ["Résumé", "Second", "Third"]
    pdfs = [tmp_path / f"{name}.pdf" for name in names]
    for path, title in zip(pdfs, ["First", "Second", "Third"]):
        _make_pdf(path, [title])
    images = [tmp_path / "blue.png", tmp_path / "red.png"]
    for path in images:
        with Image.new("RGB", (40, 32), path.stem) as image:
            image.save(path)
    output = tmp_path / "merged.pdf"
    releases = "https://github.com/Bm-nitin/PDF-Utility-Suite/releases"
    release_page = f"{releases}/tag/v2.0.0"
    download_url = f"{releases}/download/v2.0.0/PDF-Utility-Suite-2.0.0.exe"
    setup_url = f"{releases}/download/v2.0.0/PDF-Utility-Suite-Setup-2.0.0.exe"
    log_file = tmp_path / "PDFUtilitySuite" / "Logs" / "app.txt"
    monkeypatch.setenv("PDF_WPF_TEST_FILES", json.dumps(
        {"merge": pdfs, "images": images, "output": output, "release": release_page,
         "download": download_url, "setup": setup_url, "log": log_file}, default=str,
    ))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    update = {"update_available": True, "version": "2.0.0", "release_url": release_page}
    responses = iter([
        {**update, "download_url": download_url},
        {**update, "download_url": setup_url, "download_is_setup": True},
        update,
    ])
    monkeypatch.setattr(wpf_backend.update_checker, "check_for_update", lambda: next(responses))

    open_wpf = wpf_app.run
    monkeypatch.setattr(wpf_app, "run", lambda: open_wpf(entry=_UI_CHECK))
    opened_folders = []
    monkeypatch.setattr(wpf_backend.os, "startfile", opened_folders.append)
    monkeypatch.setattr(app, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(app, "OUTPUT_DIR", tmp_path / "output")
    assert app.main() == 0

    with pymupdf.open(output) as document:
        assert [page.get_text().strip() for page in document] == ["Second", "Third", "First"]
    assert ui_theme.load_theme(tmp_path / "PDFUtilitySuite" / "settings.json") == "dark"
    assert opened_folders == [log_file.parent]
    assert app_logging.log_path() == log_file
    log = log_file.read_text(encoding="utf-8")
    assert "Started merge_compress" in log and "Finished merge_compress" in log
    assert "Résumé.pdf" not in log and str(output) not in log
