"""Windows WPF event handlers and application protocol."""

CONTROLLER = r"""
$rowSource = @'
using System.ComponentModel;
using System.Windows;
using System.Windows.Media;

public sealed class PdfUtilityRow : INotifyPropertyChanged {
    public string Name { get; set; }
    public string Path { get; set; }
    public string Detail { get; set; }
    public string TypeLabel { get; set; }
    public int Page { get; set; }
    public int PageCount { get; set; }
    public long Bytes { get; set; }
    public bool Encrypted { get; set; }
    public string PreviewKind { get; set; }
    public bool PreviewRequested { get; set; }
    public Visibility DragHandle { get; set; }
    private ImageSource thumbnail;
    public ImageSource Thumbnail {
        get { return thumbnail; }
        set {
            thumbnail = value;
            var handler = PropertyChanged;
            if (handler != null) handler(this, new PropertyChangedEventArgs("Thumbnail"));
        }
    }
    public event PropertyChangedEventHandler PropertyChanged;
}
'@
Add-Type -TypeDefinition $rowSource -ReferencedAssemblies @(
    'System.dll', [Windows.DependencyObject].Assembly.Location,
    [Windows.Media.ImageSource].Assembly.Location, [Windows.Controls.ListBox].Assembly.Location
)

$window = [Windows.Markup.XamlReader]::Parse($xaml)
$names = 'MinimizeWindow ToggleWindowSize CloseWindow SettingsButton Tools Workspace TitleText Description
AddFiles FileSummary FileList EmptyQueue ListTitle RowHeading MoveUp MoveDown RemoveFile ClearFiles
ReorderOverlay ReorderSlot FloatingRow FloatingContent OptionsScroll Options StatusText Activity RunAction
SettingsView LightTheme DarkTheme ThemeStatus VersionLabel CheckUpdates UpdateAction UpdateStatus
LogPath OpenLogs LogStatus'
foreach ($name in $names -split '\s+') { Set-Variable -Name $name -Value $window.FindName($name) }
$FloatingContent.ContentTemplate = $FileList.ItemTemplate

$ui = @{
    Active = $null; States = @{}; Controls = @{}; Passwords = @()
    Busy = $false; Binding = $false; Settings = $false; Sequence = 0
    Pending = @{}; ThumbnailPending = $false; UpdateLink = $null
    Updating = $false; Theme = ''; Navigation = @{}; Ready = $false
}

function Send-Request($request, $context = $null) {
    $ui.Sequence++
    $request.id = $ui.Sequence
    $ui.Pending[[string]$request.id] = @{ Action = $request.action; Context = $context }
    $outputWriter.WriteLine(($request | ConvertTo-Json -Compress -Depth 10))
}

function Set-Theme([string]$theme) {
    $index = if ($theme -eq 'dark') { 0 } else { 1 }
    $ui.Theme = $theme
    foreach ($entry in $palette.PSObject.Properties) {
        $color = [Windows.Media.ColorConverter]::ConvertFromString($entry.Value[$index])
        $window.Resources[$entry.Name] = [Windows.Media.SolidColorBrush]::new($color)
    }
}

function Update-ThemeFromSettings([string]$theme) {
    if ($ui.Binding -or $ui.Theme -eq $theme) { return }
    Set-Theme $theme
    $ThemeStatus.Text = ''
    Send-Request @{ action = 'theme'; theme = $theme }
}

function New-State($tool) {
    $sources = [Collections.ObjectModel.ObservableCollection[object]]::new()
    $rows = $sources
    if ($tool.source -in @('reorder_pages', 'select_pages')) {
        $rows = [Collections.ObjectModel.ObservableCollection[object]]::new()
    }
    $values = @{}
    foreach ($field in $tool.fields) { $values[$field.key] = $field.default }
    return @{
        Tool = $tool; Sources = $sources; Rows = $rows; Values = $values
        Status = 'Select a file to get started.'; OutputFolder = ''; FolderLabel = $null
    }
}

function Can-Reorder {
    return (-not $ui.Busy -and -not $ui.Settings -and
            $null -ne $ui.Active -and $ui.Active.Tool.source -in @('files', 'images', 'reorder_pages'))
}

function Set-ToolStatus([string]$message) {
    $ui.Active.Status = $message
    $StatusText.Text = $message
}

function Format-FileSize([long]$length) {
    if ($length -ge 1GB) { return '{0:N1} GB' -f ($length / 1GB) }
    if ($length -ge 1MB) { return '{0:N1} MB' -f ($length / 1MB) }
    if ($length -ge 1KB) { return '{0:N1} KB' -f ($length / 1KB) }
    return "$length B"
}

function Update-Controls {
    if ($null -eq $ui.Active) { return }
    $state = $ui.Active
    $busy = $ui.Busy
    $count = $state.Sources.Count
    $index = $FileList.SelectedIndex
    $sourceMode = $state.Tool.source
    $reorder = Can-Reorder
    $pageView = $sourceMode -in @('reorder_pages', 'select_pages')
    $filesView = $sourceMode -in @('files', 'images')
    $header = if ($pageView) { 'Pages' } else { 'Files' }
    $ListTitle.Text = $header
    $RowHeading.Text = if ($pageView) { 'Page' } else { 'File name' }
    $AddFiles.Content = if ($sourceMode -eq 'images') { '+ Add images' }
                        elseif ($sourceMode -eq 'files') { '+ Add PDFs' }
                        elseif ($count) { 'Change PDF' } else { 'Select PDF' }
    $AddFiles.IsEnabled = -not $busy
    $OptionsScroll.IsEnabled = -not $busy
    $FileList.IsEnabled = -not $busy
    $Activity.Visibility = if ($busy) { 'Visible' } else { 'Collapsed' }
    $EmptyQueue.Visibility = if ($state.Rows.Count) { 'Collapsed' } else { 'Visible' }
    $EmptyQueue.Text = if ($sourceMode -eq 'images') { 'No images selected' } else { 'No PDFs selected' }
    $MoveUp.Visibility = $MoveDown.Visibility = if ($filesView -or $sourceMode -eq 'reorder_pages') {
        'Visible'
    } else { 'Collapsed' }
    $MoveUp.IsEnabled = $reorder -and $index -gt 0
    $MoveDown.IsEnabled = $reorder -and $index -ge 0 -and $index -lt ($state.Rows.Count - 1)
    $RemoveFile.Visibility = if ($filesView) { 'Visible' } else { 'Collapsed' }
    $RemoveFile.IsEnabled = -not $busy -and $index -ge 0
    $ClearFiles.IsEnabled = -not $busy -and $count -gt 0

    $action = $state.Tool.action
    $minimum = 1
    if ($state.Tool.id -eq 'merge_compress') {
        switch ($state.Values.mode) {
            'merge' { $action = 'Merge PDFs'; $minimum = 2 }
            'compress' { $action = 'Compress PDFs' }
            default { $minimum = 2 }
        }
    }
    $RunAction.Content = $action
    $RunAction.IsEnabled = -not $busy -and $count -ge $minimum -and
        ($state.Tool.id -ne 'unlock' -or $state.Sources[0].Encrypted)
    if ($count -eq 1 -and -not $filesView) {
        $FileSummary.Text = "{0}  ·  {1}" -f $state.Sources[0].Name, $state.Sources[0].Detail
    } elseif ($filesView -and $count) {
        $size = Format-FileSize (($state.Sources | Measure-Object -Property Bytes -Sum).Sum)
        if ($sourceMode -eq 'files') {
            $pages = ($state.Sources | Measure-Object -Property PageCount -Sum).Sum
            $FileSummary.Text = "$count PDFs  ·  $pages pages  ·  $size total"
        } else {
            $FileSummary.Text = "$count images  ·  $size total"
        }
    } else {
        $FileSummary.Text = ''
    }
    foreach ($button in $ui.Navigation.Values) { $button.IsEnabled = -not $busy }
    $SettingsButton.IsEnabled = -not $busy
}

function Update-OptionVisibility {
    foreach ($option in $ui.Controls.Values) {
        $visible = $true
        if ($null -ne $option.Field.when) {
            foreach ($condition in $option.Field.when.PSObject.Properties) {
                if ($ui.Active.Values[$condition.Name] -notin @($condition.Value)) { $visible = $false }
            }
        }
        $option.Panel.Visibility = if ($visible) { 'Visible' } else { 'Collapsed' }
        if ($option.Hint) {
            $value = $ui.Active.Values[$option.Field.key]
            $option.Hint.Text = $option.Field.descriptions.PSObject.Properties[$value].Value
        }
    }
}

function Write-Option([string]$key, $value) {
    $ui.Active.Values[$key] = $value
    $option = $ui.Controls[$key]
    if ($null -eq $option) { return }
    $binding = $ui.Binding
    $ui.Binding = $true
    try {
        if ($option.Control -is [Windows.Controls.ComboBox]) { $option.Control.SelectedValue = $value }
        elseif ($option.Control -is [Windows.Controls.TextBox]) { $option.Control.Text = [string]$value }
    } finally { $ui.Binding = $binding }
}

function Read-Option($control) {
    if ($ui.Binding) { return }
    $key = $control.Tag
    if ($control -is [Windows.Controls.PasswordBox]) { $value = $control.Password }
    elseif ($control -is [Windows.Controls.CheckBox]) { $value = [bool]$control.IsChecked }
    elseif ($control -is [Windows.Controls.ComboBox] -and -not $control.IsEditable) {
        $value = $control.SelectedValue
        if ($null -eq $value) { return }
    } else { $value = $control.Text }
    $ui.Active.Values[$key] = $value
    if ($key -eq 'scope' -and $ui.Active.Tool.source -eq 'select_pages') {
        if ($value -eq 'all') {
            $ui.Binding = $true
            try { $FileList.UnselectAll() } finally { $ui.Binding = $false }
        } elseif ($ui.Active.Values.pages) {
            Request-PagePlan 'pages'
        }
    }
    Update-OptionVisibility
    Update-Controls
}

function New-OptionButton([string]$name) {
    $button = [Windows.Controls.Button]::new()
    $button.Style = $window.FindResource('ButtonBase')
    $button.Content = $name
    $button.Margin = [Windows.Thickness]::new(0,5,6,0)
    return $button
}

function Build-Options {
    $Options.Children.Clear()
    $ui.Controls = @{}
    $ui.Passwords = @()
    foreach ($field in $ui.Active.Tool.fields) {
        $value = $ui.Active.Values[$field.key]
        $panel = [Windows.Controls.StackPanel]::new()
        $panel.Margin = [Windows.Thickness]::new(0,0,0,10)
        $control = $null

        if ($field.kind -eq 'check') {
            $control = [Windows.Controls.CheckBox]::new()
            $control.Content = $field.label
            $control.IsChecked = [bool]$value
            $control.Add_Checked({ param($sender) Read-Option $sender })
            $control.Add_Unchecked({ param($sender) Read-Option $sender })
        } elseif ($field.kind -eq 'password') {
            $control = [Windows.Controls.PasswordBox]::new()
            $control.Password = [string]$value
            $control.Add_PasswordChanged({ param($sender) Read-Option $sender })
        } elseif (@($field.choices).Count) {
            $control = [Windows.Controls.ComboBox]::new()
            $control.SelectedValuePath = 'Tag'
            foreach ($choice in $field.choices) {
                $item = [Windows.Controls.ComboBoxItem]::new()
                $item.Content = $choice.label
                $item.Tag = $choice.value
                [void]$control.Items.Add($item)
            }
            if ($field.kind -eq 'editable') {
                $control.IsEditable = $true
                $control.Text = [string]$value
                $control.AddHandler([Windows.Controls.TextBox]::TextChangedEvent,
                    [Windows.Controls.TextChangedEventHandler]{ param($sender) Read-Option $sender })
            } else {
                $control.SelectedValue = $value
                $control.Add_SelectionChanged({ param($sender) Read-Option $sender })
            }
        } else {
            $control = [Windows.Controls.TextBox]::new()
            $control.Text = [string]$value
            $control.Add_TextChanged({ param($sender) Read-Option $sender })
        }

        $control.Tag = $field.key
        if ($field.kind -ne 'check') {
            $label = [Windows.Controls.Label]::new()
            $label.Content = $field.label
            $label.Target = $control
            $label.Padding = [Windows.Thickness]::new(0,0,0,4)
            $label.SetResourceReference([Windows.Controls.Control]::ForegroundProperty, 'Ink')
            [void]$panel.Children.Add($label)
        }
        [void]$panel.Children.Add($control)
        $ui.Controls[$field.key] = @{ Control = $control; Panel = $panel; Field = $field }
        if ($field.descriptions) {
            $hint = [Windows.Controls.TextBlock]::new()
            $hint.TextWrapping = 'Wrap'
            $hint.FontSize = 12
            $hint.Margin = [Windows.Thickness]::new(1,5,0,0)
            $hint.SetResourceReference([Windows.Controls.TextBlock]::ForegroundProperty, 'Muted')
            [void]$panel.Children.Add($hint)
            $ui.Controls[$field.key].Hint = $hint
        }

        if ($field.kind -eq 'password') {
            $plain = [Windows.Controls.TextBox]::new()
            $plain.Tag = $field.key
            $plain.Visibility = 'Collapsed'
            $plain.Add_TextChanged({ param($sender) Read-Option $sender })
            [void]$panel.Children.Add($plain)
            $ui.Passwords += @{ Masked = $control; Plain = $plain; Key = $field.key }
        }
        if ($field.kind -in @('order', 'selection', 'color')) {
            $actions = [Windows.Controls.WrapPanel]::new()
            if ($field.kind -eq 'color') {
                $picker = New-OptionButton 'Choose color…'
                $picker.Add_Click({
                    $dialog = [Windows.Forms.ColorDialog]::new()
                    try {
                        if ($dialog.ShowDialog() -eq 'OK') {
                            Write-Option 'custom_color' ('#{0:X2}{1:X2}{2:X2}' -f $dialog.Color.R, $dialog.Color.G, $dialog.Color.B)
                        }
                    } finally { $dialog.Dispose() }
                })
                [void]$actions.Children.Add($picker)
            } else {
                $apply = New-OptionButton 'Apply'
                $apply.Tag = $field.key
                $apply.Add_Click({ param($sender) Request-PagePlan $sender.Tag })
                [void]$actions.Children.Add($apply)
                if ($field.kind -eq 'order') {
                    $reset = New-OptionButton 'Reset order'
                    $reset.Add_Click({ Apply-PagePlan @(0..($ui.Active.Rows.Count - 1)) })
                    [void]$actions.Children.Add($reset)
                }
            }
            [void]$panel.Children.Add($actions)
        }
        [void]$Options.Children.Add($panel)
    }
    if ($ui.Passwords.Count) {
        $show = [Windows.Controls.CheckBox]::new()
        $show.Content = 'Show password'
        $show.Add_Checked({ Set-PasswordVisibility $true })
        $show.Add_Unchecked({ Set-PasswordVisibility $false })
        [void]$Options.Children.Add($show)
    }
    if ($ui.Active.Tool.id -eq 'pdf_to_images') {
        $folder = New-OptionButton 'Choose output folder…'
        $folder.HorizontalAlignment = 'Left'
        $folder.Add_Click({
            $path = Choose-Folder $ui.Active.OutputFolder
            if ($path) {
                $ui.Active.OutputFolder = $path
                Update-OutputFolder
            }
        })
        [void]$Options.Children.Add($folder)
        $label = [Windows.Controls.TextBlock]::new()
        $label.TextWrapping = 'Wrap'
        $label.FontSize = 12
        $label.Margin = [Windows.Thickness]::new(0,5,0,0)
        $label.SetResourceReference([Windows.Controls.TextBlock]::ForegroundProperty, 'Muted')
        $ui.Active.FolderLabel = $label
        [void]$Options.Children.Add($label)
        Update-OutputFolder
    }
    Update-OptionVisibility
    $OptionsScroll.ScrollToTop()
}

function Set-PasswordVisibility([bool]$show) {
    $ui.Binding = $true
    try {
        foreach ($secret in $ui.Passwords) {
            if ($show) {
                $secret.Plain.Text = [string]$ui.Active.Values[$secret.Key]
                $secret.Plain.Visibility = 'Visible'
                $secret.Masked.Visibility = 'Collapsed'
            } else {
                $secret.Masked.Password = [string]$ui.Active.Values[$secret.Key]
                $secret.Masked.Visibility = 'Visible'
                $secret.Plain.Visibility = 'Collapsed'
            }
        }
    } finally { $ui.Binding = $false }
}

function Show-Tool([string]$id) {
    if ($ui.Busy) { return }
    End-RowDrag $false
    $ui.Binding = $true
    try {
        $ui.Active = $ui.States[$id]
        $ui.Settings = $false
        $SettingsView.Visibility = 'Collapsed'
        $Workspace.Visibility = 'Visible'
        $TitleText.Text = $ui.Active.Tool.name
        $Description.Text = $ui.Active.Tool.description
        $StatusText.Text = $ui.Active.Status
        $FileList.ItemsSource = $null
        $FileList.SelectionMode = if ($ui.Active.Tool.source -eq 'select_pages') { 'Multiple' } else { 'Single' }
        $FileList.ItemsSource = $ui.Active.Rows
        if ($ui.Active.Rows.Count -and $FileList.SelectionMode -eq 'Single') { $FileList.SelectedIndex = 0 }
        Build-Options
        if ($ui.Active.Tool.source -eq 'select_pages' -and $ui.Active.Values.scope -eq 'selected' -and
            $ui.Active.Values.pages -and $ui.Active.Rows.Count) {
            Request-PagePlan 'pages'
        }
    } finally { $ui.Binding = $false }
    Update-Controls
}

function Update-OrderField {
    if ($ui.Active.Tool.source -eq 'reorder_pages') {
        Write-Option 'order' (($ui.Active.Rows | ForEach-Object { $_.Page + 1 }) -join ', ')
    }
}

function Apply-PagePlan($indices) {
    if (-not $ui.Active.Rows.Count) { return }
    $previous = $ui.Binding
    $ui.Binding = $true
    try {
        if ($ui.Active.Tool.source -eq 'reorder_pages') {
            for ($slot = 0; $slot -lt @($indices).Count; $slot++) {
                for ($index = $slot; $index -lt $ui.Active.Rows.Count; $index++) {
                    if ($ui.Active.Rows[$index].Page -eq $indices[$slot]) {
                        if ($index -ne $slot) { $ui.Active.Rows.Move($index, $slot) }
                        break
                    }
                }
            }
            Update-OrderField
        } else {
            $FileList.UnselectAll()
            foreach ($row in $ui.Active.Rows) {
                if ($row.Page -in $indices) { [void]$FileList.SelectedItems.Add($row) }
            }
        }
    } finally { $ui.Binding = $previous }
}

function Request-PagePlan([string]$field) {
    if ($ui.Busy -or -not $ui.Active.Sources.Count) { return }
    $request = @{
        action = 'plan_pages'; tool = $ui.Active.Tool.id; text = $ui.Active.Values[$field]
        page_count = $ui.Active.Sources[0].PageCount
    }
    Send-Request $request $ui.Active
}

function Receive-Files($result, $state) {
    if ($state.Tool.source -notin @('files', 'images') -and @($result.rows).Count) {
        $state.Sources.Clear()
        $state.Rows.Clear()
    }
    foreach ($info in @($result.rows)) {
        $row = [PdfUtilityRow]::new()
        $row.Name = $info.name
        $row.Path = $info.path
        $row.Bytes = $info.size
        $row.Detail = "{0} · {1}" -f $info.size_label, $info.details
        $row.PageCount = [int]$info.page_count
        $row.Encrypted = [bool]$info.is_encrypted
        $row.TypeLabel = if ($state.Tool.source -eq 'images') { 'IMG' } else { 'PDF' }
        $row.DragHandle = if ($state.Tool.source -in @('files','images')) { 'Visible' } else { 'Hidden' }
        if ($state.Tool.source -eq 'images') { $row.PreviewKind = 'image' }
        $state.Sources.Add($row)
        if ($state.Tool.source -in @('reorder_pages', 'select_pages')) {
            for ($page = 0; $page -lt $row.PageCount; $page++) {
                $preview = [PdfUtilityRow]::new()
                $preview.Name = "Page $($page + 1)"
                $preview.TypeLabel = 'PAGE'
                $preview.Path = $row.Path
                $preview.Page = $page
                $preview.PreviewKind = 'pdf'
                $preview.DragHandle = if ($state.Tool.source -eq 'reorder_pages') { 'Visible' } else { 'Hidden' }
                $state.Rows.Add($preview)
            }
        }
    }
    if ($state.Tool.source -eq 'reorder_pages') { Update-OrderField }
    if ($state.Tool.source -eq 'select_pages' -and @($result.rows).Count) {
        Write-Option 'scope' 'all'
        Write-Option 'pages' ''
        Update-OptionVisibility
    }
    if ($FileList.SelectionMode -eq 'Single' -and $state.Rows.Count) { $FileList.SelectedIndex = 0 }
    $messages = @($result.errors)
    if (@($result.duplicates).Count) { $messages += 'Skipped PDFs that are already in the list.' }
    if ($state.Tool.id -eq 'unlock' -and $state.Sources.Count -and -not $state.Sources[0].Encrypted) {
        $messages += 'The selected PDF is already unlocked.'
    }
    Set-ToolStatus $(if ($messages.Count) { $messages -join " `n" } else { 'Ready.' })
    $large = @($result.rows | Where-Object { $_.size -ge 200MB })
    if ($large.Count) {
        [void][Windows.MessageBox]::Show($window, 'Some files are larger than 200 MB and may take longer to process.',
                                         'Large files', 'OK', 'Information')
    }
}

function Update-OutputFolder {
    $ui.Active.FolderLabel.Text = if ($ui.Active.OutputFolder) {
        $ui.Active.OutputFolder
    } else { 'No output folder selected' }
}

function Choose-Folder([string]$initial = '') {
    $dialog = [Windows.Forms.FolderBrowserDialog]::new()
    try {
        $dialog.Description = 'Select an output folder'
        $dialog.SelectedPath = $initial
        if ($dialog.ShowDialog() -eq 'OK') { return $dialog.SelectedPath }
        return $null
    } finally { $dialog.Dispose() }
}

function Choose-SavePath([string]$suggestion) {
    $dialog = [Microsoft.Win32.SaveFileDialog]::new()
    $dialog.DefaultExt = '.pdf'
    $dialog.Filter = 'PDF documents (*.pdf)|*.pdf'
    $dialog.FileName = $suggestion
    if ($dialog.ShowDialog($window) -ne $true) { return $null }
    $path = $dialog.FileName
    if ([IO.Path]::GetExtension($path) -ine '.pdf') {
        $path += '.pdf'
        if ([IO.File]::Exists($path) -and
            [Windows.MessageBox]::Show($window, "Replace '$([IO.Path]::GetFileName($path))'?",
                                      'File already exists', 'YesNo', 'Warning') -ne 'Yes') {
            return $null
        }
    }
    return $path
}

function Run-Operation($request, $destination) {
    $request.action = 'execute'
    $request.destination = $destination
    Set-ToolStatus 'Processing…'
    Send-Request $request
}

function Begin-Operation {
    if (-not $RunAction.IsEnabled -or $ui.Busy) { return }
    $state = $ui.Active
    $paths = @($state.Sources | ForEach-Object { $_.Path })
    $request = @{ tool = $state.Tool.id; paths = $paths; options = $state.Values.Clone() }
    $target = $state.Tool.destination
    if ($state.Tool.id -eq 'merge_compress' -and $state.Values.mode -eq 'compress' -and $paths.Count -gt 1) {
        $target = 'folder'
    }
    if ($target -eq 'folder') {
        $destination = $state.OutputFolder
        if (-not $destination) { $destination = Choose-Folder }
        if (-not $destination) { return }
        if ($state.Tool.id -eq 'pdf_to_images') {
            $state.OutputFolder = $destination
            Update-OutputFolder
        }
    }
    $ui.Busy = $true
    Update-Controls
    if ($target -eq 'save') {
        $request.action = 'filename'
        Send-Request $request $request
    } else {
        Run-Operation $request $destination
    }
}

function Initialize-Tools($info) {
    $ui.Binding = $true
    Set-Theme $info.theme
    $LightTheme.IsChecked = $info.theme -eq 'light'
    $DarkTheme.IsChecked = $info.theme -eq 'dark'
    $VersionLabel.Text = "Installed version $($info.version)"
    $LogPath.Text = $info.log_path
    $OpenLogs.IsEnabled = [bool]$info.log_path
    if (-not $info.log_path) { $LogStatus.Text = 'The log folder is unavailable.' }
    foreach ($tool in $info.tools) {
        $ui.States[$tool.id] = New-State $tool
        $button = [Windows.Controls.RadioButton]::new()
        $button.Style = $window.FindResource('ModeButton')
        $button.Content = $tool.name
        $button.Tag = $tool.id
        $button.Height = 36
        $button.GroupName = 'Navigation'
        $button.Add_Checked({ param($sender) Show-Tool $sender.Tag })
        [void]$Tools.Children.Add($button)
        $ui.Navigation[$tool.id] = $button
    }
    $ui.Binding = $false
    $ui.Navigation.merge_compress.IsChecked = $true
    $ui.Ready = $true
}

function Receive-Message($message) {
    $id = [string]$message.id
    $pending = $ui.Pending[$id]
    if ($null -eq $pending) { return }
    if ($message.kind -eq 'progress') {
        if ($pending.Action -in @('import', 'execute')) { Set-ToolStatus $message.value }
        return
    }
    $ui.Pending.Remove($id)
    $result = $message.value
    if ($message.kind -eq 'error') {
        switch ($pending.Action) {
            'describe' { throw $result }
            'thumbnail' { $ui.ThumbnailPending = $false }
            'theme' { $ThemeStatus.Text = $result }
            'open_logs' { $LogStatus.Text = $result }
            'updates' {
                $ui.Updating = $false
                $CheckUpdates.IsEnabled = $true
                $UpdateStatus.Text = $result
            }
            'plan_pages' {
                if ($ui.Active -eq $pending.Context) { Set-ToolStatus $result }
            }
            default { $ui.Busy = $false; Set-ToolStatus $result; Update-Controls }
        }
        return
    }

    switch ($pending.Action) {
        'describe' { Initialize-Tools $result }
        'import' {
            $ui.Busy = $false
            $ui.Binding = $true
            try { Receive-Files $result $pending.Context } finally { $ui.Binding = $false }
            Update-Controls
        }
        'filename' {
            $destination = Choose-SavePath $result
            if ($destination) { Run-Operation $pending.Context $destination }
            else { $ui.Busy = $false; Update-Controls }
        }
        'execute' {
            $ui.Busy = $false
            $paths = @($result.paths)
            $messages = @()
            if ($paths.Count -eq 1) { $messages += "Saved $($paths[0])" }
            elseif ($paths.Count) { $messages += "Saved $($paths.Count) files to $([IO.Path]::GetDirectoryName($paths[0]))."}
            if ($result.sizes -and $result.sizes.Count -eq 2) {
                $before = Format-FileSize $result.sizes[0]
                $after = Format-FileSize $result.sizes[1]
                $messages += "$before → $after"
            }
            $messages += @($result.errors | Where-Object { $_ })
            Set-ToolStatus ($messages -join " `n")
            foreach ($secret in $ui.Passwords) {
                $secret.Masked.Clear()
                $secret.Plain.Clear()
            }
            Update-Controls
        }
        'plan_pages' {
            if ($ui.Active -eq $pending.Context) {
                Apply-PagePlan @($result)
                Set-ToolStatus 'Page selection updated.'
            }
        }
        'thumbnail' {
            $ui.ThumbnailPending = $false
            $stream = [IO.MemoryStream]::new([Convert]::FromBase64String($result))
            try {
                $image = [Windows.Media.Imaging.BitmapImage]::new()
                $image.BeginInit()
                $image.CacheOption = 'OnLoad'
                $image.StreamSource = $stream
                $image.EndInit()
                $image.Freeze()
                $pending.Context.Thumbnail = $image
            } finally { $stream.Dispose() }
        }
        'updates' {
            $ui.Updating = $false
            $CheckUpdates.IsEnabled = $true
            if ($result.update_available) {
                $UpdateStatus.Text = "Version $($result.version) is available."
                if ($result.download_url) {
                    $ui.UpdateLink = $result.download_url
                    if ($result.download_is_setup) {
                        $UpdateAction.Content = 'Download Setup'
                        $UpdateStatus.Text += ' Close PDF Utility Suite before running Setup.'
                    } else {
                        $UpdateAction.Content = 'Download Update'
                    }
                } else {
                    $ui.UpdateLink = $result.release_url
                    $UpdateAction.Content = 'Open release page'
                }
                $UpdateAction.Visibility = 'Visible'
            } else { $UpdateStatus.Text = 'You are using the latest available version.' }
        }
    }
}

function Request-VisibleThumbnail {
    if (-not $ui.Ready -or $ui.Busy -or $ui.Settings -or $ui.ThumbnailPending -or $drag.Active) { return }
    $scroll = $FileList.Template.FindName('QueueScroll', $FileList)
    if ($null -eq $scroll -or -not $FileList.ActualHeight) { return }
    $first = [int][Math]::Max(0, [Math]::Floor($scroll.VerticalOffset / 64))
    $last = [int][Math]::Min($ui.Active.Rows.Count - 1, $first + [Math]::Ceiling($FileList.ActualHeight / 64))
    for ($index = $first; $index -le $last; $index++) {
        $row = $ui.Active.Rows[$index]
        if (-not $row.PreviewKind -or $row.PreviewRequested) { continue }
        $row.PreviewRequested = $true
        $ui.ThumbnailPending = $true
        Send-Request @{ action = 'thumbnail'; path = $row.Path; kind = $row.PreviewKind; page = $row.Page } $row
        return
    }
}

$AddFiles.Add_Click({
    if ($ui.Busy) { return }
    $state = $ui.Active
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $state.Tool.source -in @('files', 'images')
    $dialog.Filter = if ($state.Tool.source -eq 'images') {
        'Images (*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff;*.webp)|*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff;*.webp'
    } else { 'PDF documents (*.pdf)|*.pdf' }
    if ($dialog.ShowDialog($window) -ne $true) { return }
    $ui.Busy = $true
    Update-Controls
    Set-ToolStatus 'Reading files…'
    Send-Request @{
        action = 'import'; tool = $state.Tool.id; paths = @($dialog.FileNames)
        existing = @($state.Sources | ForEach-Object { $_.Path })
    } $state
})
$FileList.Add_SelectionChanged({
    if ($ui.Binding -or $null -eq $ui.Active) { return }
    if ($ui.Active.Tool.source -eq 'select_pages' -and $FileList.SelectedItems.Count) {
        Write-Option 'scope' 'selected'
        $pages = ($ui.Active.Rows | Where-Object { $FileList.SelectedItems.Contains($_) } |
                  ForEach-Object { $_.Page + 1 }) -join ', '
        Write-Option 'pages' $pages
        Update-OptionVisibility
    } elseif ($ui.Active.Tool.source -eq 'select_pages' -and $ui.Active.Values.scope -eq 'selected') {
        Write-Option 'pages' ''
    }
    Update-Controls
})
$MoveUp.Add_Click({ Move-SelectedFile -1 })
$MoveDown.Add_Click({ Move-SelectedFile 1 })
$RemoveFile.Add_Click({
    $index = $FileList.SelectedIndex
    if ($ui.Busy -or $index -lt 0) { return }
    $ui.Active.Rows.RemoveAt($index)
    $FileList.SelectedIndex = [Math]::Min($index, $ui.Active.Rows.Count - 1)
    Update-Controls
})
$ClearFiles.Add_Click({
    if ($ui.Busy) { return }
    $ui.Active.Sources.Clear()
    $ui.Active.Rows.Clear()
    if ($ui.Active.Tool.source -eq 'reorder_pages') { Write-Option 'order' '' }
    if ($ui.Active.Tool.source -eq 'select_pages') { Write-Option 'pages' '' }
    Set-ToolStatus 'Select a file to get started.'
    Update-Controls
})
$RunAction.Add_Click({ Begin-Operation })
$SettingsButton.Add_Checked({
    if ($ui.Busy) { return }
    End-RowDrag $false
    $ui.Settings = $true
    $Workspace.Visibility = 'Collapsed'
    $SettingsView.Visibility = 'Visible'
})
$LightTheme.Add_Checked({ Update-ThemeFromSettings 'light' })
$DarkTheme.Add_Checked({ Update-ThemeFromSettings 'dark' })
$OpenLogs.Add_Click({
    $LogStatus.Text = ''
    Send-Request @{ action = 'open_logs' }
})

function Open-UpdateLink([string]$url) {
    [void][Diagnostics.Process]::Start($url)
}

$CheckUpdates.Add_Click({
    if ($ui.Updating) { return }
    $ui.Updating = $true
    $CheckUpdates.IsEnabled = $false
    $ui.UpdateLink = $null
    $UpdateAction.Visibility = 'Collapsed'
    $UpdateStatus.Text = 'Checking for updates…'
    Send-Request @{ action = 'updates' }
})
$UpdateAction.Add_Click({
    if ($ui.UpdateLink) {
        try { Open-UpdateLink $ui.UpdateLink }
        catch { $UpdateStatus.Text = "Open this link in your browser: $($ui.UpdateLink)" }
    }
})

function Start-Interface {
    $ui.Line = $inputReader.ReadLineAsync()
    $pump = [Windows.Threading.DispatcherTimer]::new()
    $pump.Interval = [TimeSpan]::FromMilliseconds(25)
    $pump.Add_Tick({
        while ($ui.Line.IsCompleted) {
            $line = $ui.Line.GetAwaiter().GetResult()
            if ($null -eq $line) { $window.Close(); return }
            $ui.Line = $inputReader.ReadLineAsync()
            Receive-Message ($line | ConvertFrom-Json)
        }
        Request-VisibleThumbnail
    })
    $pump.Start()
    Send-Request @{ action = 'describe' }
    try { [void]$window.ShowDialog() } finally { $pump.Stop() }
}
"""
