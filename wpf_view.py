"""WPF view and drag interaction for the Windows application."""

# Each pair contains the dark and light values for a shared WPF brush.
PALETTE = {
    "Canvas": ("#0E1522", "#EDF1F7"),
    "Sidebar": ("#111B2C", "#F6F8FC"),
    "TitleBar": ("#101828", "#E7EDF7"),
    "Ink": ("#E5EDF9", "#18243A"),
    "Muted": ("#A6B5CB", "#52637B"),
    "Line": ("#303E56", "#DCE4EF"),
    "Blue": ("#90ADFF", "#2457CE"),
    "Surface": ("#182132", "#FFFFFF"),
    "Control": ("#202B40", "#F1F4F9"),
    "Input": ("#111929", "#FBFCFE"),
    "Hover": ("#23314A", "#F1F5FC"),
    "Selection": ("#243758", "#EDF3FF"),
    "OnAccent": ("#10192A", "#FFFFFF"),
    "NavText": ("#B8C6DC", "#495A74"),
    "Mark": ("#81A7FF", "#285BDA"),
    "Outline": ("#3C5680", "#D3E0FA"),
    "SelectedBorder": ("#47669C", "#B8CBF5"),
    "TagBackground": ("#422538", "#FDECF0"),
    "TagForeground": ("#F7ABC3", "#A63050"),
    "Track": ("#566B8D", "#95A7C2"),
    "DropSlot": ("#1A2944", "#EEF4FF"),
    "Floating": ("#2A3F65", "#E8F0FF"),
    "Ring": ("#7285A5", "#9EAEC5"),
    "PresetOutline": ("#5B77AE", "#92AEF1"),
    "SlotBorder": ("#7290D4", "#7E9DE6"),
}


XAML = r"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:shell="clr-namespace:System.Windows.Shell;assembly=PresentationFramework"
        Title="PDF Utility Suite" Width="1130" Height="752"
        MinWidth="940" MinHeight="660" WindowStartupLocation="CenterScreen"
        WindowStyle="SingleBorderWindow" FontFamily="Segoe UI" FontSize="14" Background="{DynamicResource Canvas}"
        TextOptions.TextFormattingMode="Display"
        UseLayoutRounding="True" SnapsToDevicePixels="True">
    <!-- Keep the native frame style so Windows maximizes to the monitor's work area. -->
    <shell:WindowChrome.WindowChrome>
        <shell:WindowChrome CaptionHeight="31" ResizeBorderThickness="6"
                            GlassFrameThickness="0" CornerRadius="0" UseAeroCaptionButtons="False"/>
    </shell:WindowChrome.WindowChrome>
    <Window.Resources>
        <SolidColorBrush x:Key="Canvas" Color="#0E1522"/>
        <SolidColorBrush x:Key="Sidebar" Color="#111B2C"/>
        <SolidColorBrush x:Key="TitleBar" Color="#101828"/>
        <SolidColorBrush x:Key="Ink" Color="#E5EDF9"/>
        <SolidColorBrush x:Key="Muted" Color="#A6B5CB"/>
        <SolidColorBrush x:Key="Line" Color="#303E56"/>
        <SolidColorBrush x:Key="Blue" Color="#90ADFF"/>
        <SolidColorBrush x:Key="Surface" Color="#182132"/>
        <SolidColorBrush x:Key="Control" Color="#202B40"/>
        <SolidColorBrush x:Key="Input" Color="#111929"/>
        <SolidColorBrush x:Key="Hover" Color="#23314A"/>
        <SolidColorBrush x:Key="Selection" Color="#243758"/>
        <SolidColorBrush x:Key="OnAccent" Color="#10192A"/>
        <SolidColorBrush x:Key="NavText" Color="#B8C6DC"/>
        <SolidColorBrush x:Key="Mark" Color="#81A7FF"/>
        <SolidColorBrush x:Key="Outline" Color="#3C5680"/>
        <SolidColorBrush x:Key="SelectedBorder" Color="#47669C"/>
        <SolidColorBrush x:Key="TagBackground" Color="#422538"/>
        <SolidColorBrush x:Key="TagForeground" Color="#F7ABC3"/>
        <SolidColorBrush x:Key="Track" Color="#566B8D"/>
        <SolidColorBrush x:Key="DropSlot" Color="#1A2944"/>
        <SolidColorBrush x:Key="Floating" Color="#2A3F65"/>
        <SolidColorBrush x:Key="Ring" Color="#7285A5"/>
        <SolidColorBrush x:Key="PresetOutline" Color="#5B77AE"/>
        <SolidColorBrush x:Key="SlotBorder" Color="#7290D4"/>
        <Style TargetType="TextBlock">
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="VerticalAlignment" Value="Center"/>
        </Style>
        <Style x:Key="Card" TargetType="Border">
            <Setter Property="Background" Value="{DynamicResource Surface}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="CornerRadius" Value="14"/>
            <Setter Property="Padding" Value="20"/>
        </Style>
        <Style x:Key="ButtonBase" TargetType="Button">
            <Setter Property="Background" Value="{DynamicResource Control}"/>
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="14,8"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="Surface" Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="7" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="Surface" Property="Opacity" Value=".84"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter TargetName="Surface" Property="Opacity" Value=".72"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter TargetName="Surface" Property="Opacity" Value=".43"/>
                            </Trigger>
                            <Trigger Property="IsKeyboardFocused" Value="True">
                                <Setter TargetName="Surface" Property="BorderBrush" Value="{DynamicResource Blue}"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="PrimaryButton" TargetType="Button" BasedOn="{StaticResource ButtonBase}">
            <Setter Property="Background" Value="{DynamicResource Blue}"/>
            <Setter Property="Foreground" Value="{DynamicResource OnAccent}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Blue}"/>
            <Setter Property="Padding" Value="21,11"/>
        </Style>
        <Style x:Key="CaptionButton" TargetType="Button">
            <Setter Property="Width" Value="44"/>
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="Background" Value="Transparent"/>
            <Setter Property="FontFamily" Value="Segoe MDL2 Assets"/>
            <Setter Property="FontSize" Value="10"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border x:Name="CaptionSurface" Background="{TemplateBinding Background}">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="CaptionSurface" Property="Background" Value="{DynamicResource Hover}"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter TargetName="CaptionSurface" Property="Background" Value="{DynamicResource Selection}"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style TargetType="ToolTip">
            <Setter Property="Background" Value="{DynamicResource Control}"/>
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="Padding" Value="6,4"/>
        </Style>
        <Style x:Key="ScrollPageButton" TargetType="RepeatButton">
            <Setter Property="Focusable" Value="False"/>
            <Setter Property="IsTabStop" Value="False"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="RepeatButton">
                        <Border Background="Transparent"/>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style TargetType="ScrollBar">
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ScrollBar">
                        <Border Background="{DynamicResource Input}" CornerRadius="5">
                            <Track x:Name="PART_Track" IsDirectionReversed="True">
                                <Track.DecreaseRepeatButton>
                                    <RepeatButton Command="ScrollBar.PageUpCommand" Style="{StaticResource ScrollPageButton}"/>
                                </Track.DecreaseRepeatButton>
                                <Track.Thumb>
                                    <Thumb>
                                        <Thumb.Template>
                                            <ControlTemplate TargetType="Thumb">
                                                <Border Background="{DynamicResource Track}" CornerRadius="4" Margin="1"/>
                                            </ControlTemplate>
                                        </Thumb.Template>
                                    </Thumb>
                                </Track.Thumb>
                                <Track.IncreaseRepeatButton>
                                    <RepeatButton Command="ScrollBar.PageDownCommand" Style="{StaticResource ScrollPageButton}"/>
                                </Track.IncreaseRepeatButton>
                            </Track>
                        </Border>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="ModeButton" TargetType="RadioButton">
            <Setter Property="Foreground" Value="{DynamicResource NavText}"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Margin" Value="0,0,0,5"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="RadioButton">
                        <Border x:Name="NavSurface" Background="Transparent" CornerRadius="7"
                                BorderThickness="1" BorderBrush="Transparent">
                            <Grid>
                                <Border x:Name="NavMark" Background="Transparent"
                                        HorizontalAlignment="Left" Width="3" Margin="0,10,0,10"
                                        CornerRadius="2"/>
                                <ContentPresenter VerticalAlignment="Center" Margin="14,0,6,0"/>
                            </Grid>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="NavSurface" Property="Background" Value="{DynamicResource Hover}"/>
                                <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
                            </Trigger>
                            <Trigger Property="IsChecked" Value="True">
                                <Setter TargetName="NavSurface" Property="Background" Value="{DynamicResource Selection}"/>
                                <Setter TargetName="NavMark" Property="Background" Value="{DynamicResource Mark}"/>
                                <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
                            </Trigger>
                            <Trigger Property="IsKeyboardFocused" Value="True">
                                <Setter TargetName="NavSurface" Property="BorderBrush" Value="{DynamicResource Mark}"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style x:Key="PresetButton" TargetType="RadioButton">
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Margin" Value="0,0,0,7"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="RadioButton">
                        <Border x:Name="PresetSurface" CornerRadius="8" Padding="11,9"
                                BorderBrush="{DynamicResource Line}" BorderThickness="1"
                                Background="{DynamicResource Surface}">
                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="24"/>
                                    <ColumnDefinition/>
                                </Grid.ColumnDefinitions>
                                <Grid Width="14" Height="14" HorizontalAlignment="Left"
                                      VerticalAlignment="Center">
                                    <Ellipse x:Name="Ring" Stroke="{DynamicResource Ring}" StrokeThickness="1.4"/>
                                    <Ellipse x:Name="Dot" Fill="{DynamicResource Blue}" Width="6"
                                             Height="6" Visibility="Hidden"/>
                                </Grid>
                                <StackPanel Grid.Column="1">
                                    <ContentPresenter TextElement.Foreground="{DynamicResource Ink}"
                                                      TextElement.FontWeight="SemiBold"/>
                                    <TextBlock FontSize="12" Margin="0,2,0,0"
                                               Foreground="{DynamicResource Muted}"
                                               Text="{Binding Tag, RelativeSource={RelativeSource TemplatedParent}}"/>
                                </StackPanel>
                            </Grid>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter TargetName="PresetSurface" Property="Background" Value="{DynamicResource Hover}"/>
                            </Trigger>
                            <Trigger Property="IsChecked" Value="True">
                                <Setter TargetName="PresetSurface" Property="Background" Value="{DynamicResource Selection}"/>
                                <Setter TargetName="PresetSurface" Property="BorderBrush" Value="{DynamicResource PresetOutline}"/>
                                <Setter TargetName="Ring" Property="Stroke" Value="{DynamicResource Blue}"/>
                                <Setter TargetName="Dot" Property="Visibility" Value="Visible"/>
                            </Trigger>
                            <Trigger Property="IsKeyboardFocused" Value="True">
                                <Setter TargetName="PresetSurface" Property="BorderBrush" Value="{DynamicResource Blue}"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style TargetType="TextBox">
            <Setter Property="Background" Value="{DynamicResource Input}"/>
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="CaretBrush" Value="{DynamicResource Ink}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="9,7"/>
        </Style>
        <Style TargetType="PasswordBox">
            <Setter Property="Background" Value="{DynamicResource Input}"/>
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="CaretBrush" Value="{DynamicResource Ink}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="9,7"/>
        </Style>
        <Style TargetType="CheckBox">
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="Margin" Value="0,1,0,10"/>
        </Style>
        <Style TargetType="ComboBoxItem">
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="Padding" Value="9,6"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ComboBoxItem">
                        <Border x:Name="ItemSurface" Background="{DynamicResource Surface}" Padding="{TemplateBinding Padding}">
                            <ContentPresenter TextElement.Foreground="{DynamicResource Ink}"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsHighlighted" Value="True">
                                <Setter TargetName="ItemSurface" Property="Background" Value="{DynamicResource Selection}"/>
                            </Trigger>
                            <Trigger Property="IsSelected" Value="True">
                                <Setter TargetName="ItemSurface" Property="Background" Value="{DynamicResource Selection}"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
        <Style TargetType="ComboBox">
            <Setter Property="Foreground" Value="{DynamicResource Ink}"/>
            <Setter Property="Background" Value="{DynamicResource Input}"/>
            <Setter Property="BorderBrush" Value="{DynamicResource Line}"/>
            <Setter Property="MinHeight" Value="34"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ComboBox">
                        <Grid>
                            <ToggleButton Focusable="False" ClickMode="Press"
                                          IsChecked="{Binding IsDropDownOpen, Mode=TwoWay, RelativeSource={RelativeSource TemplatedParent}}">
                                <ToggleButton.Template>
                                    <ControlTemplate TargetType="ToggleButton">
                                        <Border CornerRadius="6" Background="{DynamicResource Input}"
                                                BorderThickness="1" BorderBrush="{DynamicResource Line}">
                                            <Path Data="M0,0 L4,4 8,0" Stroke="{DynamicResource Muted}" StrokeThickness="1.6"
                                                  HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,12,0"/>
                                        </Border>
                                    </ControlTemplate>
                                </ToggleButton.Template>
                            </ToggleButton>
                            <ContentPresenter x:Name="ChoiceText" Margin="10,4,27,4" VerticalAlignment="Center"
                                              IsHitTestVisible="False" TextElement.Foreground="{DynamicResource Ink}"
                                              Content="{TemplateBinding SelectionBoxItem}"
                                              ContentTemplate="{TemplateBinding SelectionBoxItemTemplate}"/>
                            <TextBox x:Name="PART_EditableTextBox" Margin="3,1,27,1" BorderThickness="0"
                                     Padding="7,5" VerticalContentAlignment="Center" Visibility="Collapsed"/>
                            <Popup x:Name="PART_Popup" Placement="Bottom" IsOpen="{TemplateBinding IsDropDownOpen}"
                                   AllowsTransparency="True" Focusable="False" PopupAnimation="Fade">
                                <Border Background="{DynamicResource Surface}" BorderBrush="{DynamicResource Line}"
                                        BorderThickness="1" CornerRadius="6" MinWidth="{TemplateBinding ActualWidth}"
                                        MaxWidth="420" MaxHeight="230" Padding="2">
                                    <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
                                        <ItemsPresenter/>
                                    </ScrollViewer>
                                </Border>
                            </Popup>
                        </Grid>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsEditable" Value="True">
                                <Setter TargetName="ChoiceText" Property="Visibility" Value="Collapsed"/>
                                <Setter TargetName="PART_EditableTextBox" Property="Visibility" Value="Visible"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>

    <Grid Background="{DynamicResource Canvas}">
        <Grid.RowDefinitions>
            <RowDefinition Height="37"/>
            <RowDefinition/>
        </Grid.RowDefinitions>
        <Grid.ColumnDefinitions>
            <ColumnDefinition Width="220"/>
            <ColumnDefinition/>
        </Grid.ColumnDefinitions>

        <Border Grid.ColumnSpan="2" Background="{DynamicResource TitleBar}"
                BorderBrush="{DynamicResource Line}" BorderThickness="0,0,0,1">
            <DockPanel>
                <StackPanel DockPanel.Dock="Right" Orientation="Horizontal" Margin="0,5,5,0"
                            shell:WindowChrome.IsHitTestVisibleInChrome="True">
                    <Button x:Name="MinimizeWindow" Content="&#xE921;" ToolTip="Minimize"
                            AutomationProperties.Name="Minimize" Style="{StaticResource CaptionButton}"/>
                    <Button x:Name="ToggleWindowSize" Content="&#xE922;" ToolTip="Maximize"
                            AutomationProperties.Name="Maximize" Style="{StaticResource CaptionButton}"/>
                    <Button x:Name="CloseWindow" Content="&#xE8BB;" ToolTip="Close"
                            AutomationProperties.Name="Close" Style="{StaticResource CaptionButton}"/>
                </StackPanel>
                <TextBlock Text="PDF Utility Suite" Margin="17,0,0,0" FontSize="12"
                           Foreground="{DynamicResource Muted}"/>
            </DockPanel>
        </Border>

        <Border Grid.Row="1" Background="{DynamicResource Sidebar}" Padding="13,17"
                BorderBrush="{DynamicResource Line}" BorderThickness="0,0,1,0">
            <DockPanel>
                <StackPanel DockPanel.Dock="Top" Orientation="Horizontal" Margin="4,0,0,21">
                    <Border Width="34" Height="34" Background="#2D61DE" CornerRadius="8">
                        <Path Width="14" Height="18" Stretch="Uniform" Stroke="White" StrokeThickness="1.6"
                              StrokeLineJoin="Round" Data="M1,1 H9 L13,5 V17 H1 Z M9,1 V5 H13 M4,9 H10 M4,12 H10"/>
                    </Border>
                    <TextBlock Text="PDF Utility Suite" Margin="9,0,0,0" FontWeight="SemiBold"/>
                </StackPanel>
                <RadioButton x:Name="SettingsButton" DockPanel.Dock="Bottom"
                             GroupName="Navigation" Content="⚙  Settings" Height="42"
                             Margin="0,12,0,0" Style="{StaticResource ModeButton}"/>
                <ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled" Focusable="False">
                    <StackPanel x:Name="Tools"/>
                </ScrollViewer>
            </DockPanel>
        </Border>

        <Grid Grid.Row="1" Grid.Column="1" Margin="25,21,25,17">
            <Grid x:Name="Workspace">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <StackPanel Margin="0,0,0,18">
                    <TextBlock x:Name="TitleText" FontSize="28" FontWeight="SemiBold"/>
                    <TextBlock x:Name="Description" Foreground="{DynamicResource Muted}"
                               TextWrapping="Wrap" Margin="0,4,0,0"/>
                </StackPanel>

                <Grid Grid.Row="1">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition/>
                        <ColumnDefinition Width="14"/>
                        <ColumnDefinition Width="305"/>
                    </Grid.ColumnDefinitions>
                    <Border Style="{StaticResource Card}">
                        <Grid>
                            <Grid.RowDefinitions>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition Height="Auto"/>
                                <RowDefinition/>
                                <RowDefinition Height="Auto"/>
                            </Grid.RowDefinitions>
                            <DockPanel Margin="0,0,0,12">
                                <Button x:Name="AddFiles" DockPanel.Dock="Right" Content="+ Add PDFs"
                                        Style="{StaticResource ButtonBase}" Background="{DynamicResource Selection}"
                                        Foreground="{DynamicResource Blue}" BorderBrush="{DynamicResource Outline}"
                                        VerticalAlignment="Center"/>
                                <StackPanel Margin="0,0,7,0">
                                    <TextBlock x:Name="ListTitle" Text="Files" FontSize="18" FontWeight="SemiBold"/>
                                    <TextBlock x:Name="FileSummary" FontSize="12" TextTrimming="CharacterEllipsis"
                                               Foreground="{DynamicResource Muted}" Margin="0,3,0,0"/>
                                </StackPanel>
                            </DockPanel>
                            <Border Grid.Row="1" Background="{DynamicResource Control}" CornerRadius="6"
                                    Padding="12,7" Margin="0,0,0,4">
                                <DockPanel>
                                    <TextBlock DockPanel.Dock="Right" Text="Details" FontSize="12"
                                               Foreground="{DynamicResource Muted}"/>
                                    <TextBlock x:Name="RowHeading" Text="File name" FontSize="12"
                                               Foreground="{DynamicResource Muted}"/>
                                </DockPanel>
                            </Border>
                            <Grid Grid.Row="2">
                <ListBox x:Name="FileList" BorderThickness="0" Background="Transparent"
                         VirtualizingPanel.ScrollUnit="Pixel"
                         AutomationProperties.Name="PDF file order">
                    <ListBox.Template>
                        <ControlTemplate TargetType="ListBox">
                            <ScrollViewer x:Name="QueueScroll" Background="Transparent" Focusable="False"
                                          VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled"
                                          CanContentScroll="True">
                                <ItemsPresenter/>
                            </ScrollViewer>
                        </ControlTemplate>
                    </ListBox.Template>
                    <ListBox.ItemContainerStyle>
                        <Style TargetType="ListBoxItem">
                            <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                            <Setter Property="Height" Value="60"/>
                            <Setter Property="Padding" Value="0"/>
                            <Setter Property="Margin" Value="0,2"/>
                            <Setter Property="Template">
                                <Setter.Value>
                                    <ControlTemplate TargetType="ListBoxItem">
                                        <Border x:Name="FileSurface" CornerRadius="7" Padding="11,6"
                                                Background="Transparent" BorderThickness="1"
                                                BorderBrush="Transparent">
                                            <ContentPresenter/>
                                        </Border>
                                        <ControlTemplate.Triggers>
                                            <Trigger Property="IsMouseOver" Value="True">
                                                <Setter TargetName="FileSurface" Property="Background" Value="{DynamicResource Hover}"/>
                                            </Trigger>
                                            <Trigger Property="IsSelected" Value="True">
                                                <Setter TargetName="FileSurface" Property="Background" Value="{DynamicResource Selection}"/>
                                                <Setter TargetName="FileSurface" Property="BorderBrush" Value="{DynamicResource SelectedBorder}"/>
                                            </Trigger>
                                            <Trigger Property="IsKeyboardFocused" Value="True">
                                                <Setter TargetName="FileSurface" Property="BorderBrush" Value="{DynamicResource Blue}"/>
                                            </Trigger>
                                        </ControlTemplate.Triggers>
                                    </ControlTemplate>
                                </Setter.Value>
                            </Setter>
                        </Style>
                    </ListBox.ItemContainerStyle>
                    <ListBox.ItemTemplate>
                        <DataTemplate>
                            <Grid>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="21"/>
                                    <ColumnDefinition Width="54"/>
                                    <ColumnDefinition/>
                                    <ColumnDefinition Width="120"/>
                                </Grid.ColumnDefinitions>
                                <Border Background="Transparent" Cursor="SizeAll" ToolTip="Drag to reorder" Visibility="{Binding DragHandle}">
                                    <Path HorizontalAlignment="Left" VerticalAlignment="Center"
                                          Stroke="{DynamicResource Muted}" StrokeThickness="1.3"
                                          StrokeStartLineCap="Round" StrokeEndLineCap="Round"
                                          Data="M 1,1 H 9 M 1,5 H 9 M 1,9 H 9"/>
                                </Border>
                                <Grid Grid.Column="1" Width="44" Height="42" HorizontalAlignment="Left">
                                    <Border CornerRadius="6" Background="{DynamicResource TagBackground}">
                                        <TextBlock Text="{Binding TypeLabel}" FontSize="9" FontWeight="Bold" VerticalAlignment="Center"
                                                   Foreground="{DynamicResource TagForeground}" HorizontalAlignment="Center"/>
                                    </Border>
                                    <Image Source="{Binding Thumbnail}" Stretch="Uniform" Margin="1"/>
                                </Grid>
                                <TextBlock Grid.Column="2" Text="{Binding Name}" FontWeight="SemiBold" VerticalAlignment="Center"
                                           Foreground="{DynamicResource Ink}"
                                           TextTrimming="CharacterEllipsis" ToolTip="{Binding Name}"/>
                                <TextBlock Grid.Column="3" Text="{Binding Detail}" FontSize="12" VerticalAlignment="Center"
                                           Foreground="{DynamicResource Muted}" TextAlignment="Right"/>
                            </Grid>
                        </DataTemplate>
                    </ListBox.ItemTemplate>
                </ListBox>
                <TextBlock x:Name="EmptyQueue" Text="No files selected" HorizontalAlignment="Center"
                           Foreground="{DynamicResource Muted}" IsHitTestVisible="False"/>
                <Canvas x:Name="ReorderOverlay" IsHitTestVisible="False" ClipToBounds="True"
                        Visibility="Collapsed">
                    <Rectangle x:Name="ReorderSlot" Fill="{DynamicResource DropSlot}" Stroke="{DynamicResource SlotBorder}"
                               StrokeThickness="1" StrokeDashArray="4,3" RadiusX="7" RadiusY="7"/>
                    <Border x:Name="FloatingRow" Background="{DynamicResource Floating}" BorderBrush="{DynamicResource Blue}"
                            BorderThickness="1" CornerRadius="7" Padding="11,6" Opacity=".93">
                        <Border.Effect>
                            <DropShadowEffect Color="#060A13" BlurRadius="16" ShadowDepth="4" Opacity=".65"/>
                        </Border.Effect>
                        <ContentPresenter x:Name="FloatingContent"/>
                    </Border>
                </Canvas>
                            </Grid>
                            <Border Grid.Row="3" BorderBrush="{DynamicResource Line}" BorderThickness="0,1,0,0"
                                    Margin="0,10,0,0" Padding="0,10,0,0">
                                <WrapPanel>
                                    <Button x:Name="MoveUp" Content="↑ Move up" Style="{StaticResource ButtonBase}" Margin="0,0,6,4"/>
                                    <Button x:Name="MoveDown" Content="↓ Move down" Style="{StaticResource ButtonBase}" Margin="0,0,6,4"/>
                                    <Button x:Name="RemoveFile" Content="Remove" Style="{StaticResource ButtonBase}" Margin="0,0,6,4"/>
                                    <Button x:Name="ClearFiles" Content="Clear" Style="{StaticResource ButtonBase}" Margin="0,0,0,4"/>
                                </WrapPanel>
                            </Border>
                        </Grid>
                    </Border>

                    <Border Grid.Column="2" Style="{StaticResource Card}">
                        <DockPanel>
                            <TextBlock DockPanel.Dock="Top" Text="Options" FontSize="18"
                                       FontWeight="SemiBold" Margin="0,0,0,12"/>
                            <ScrollViewer x:Name="OptionsScroll" VerticalScrollBarVisibility="Auto"
                                          HorizontalScrollBarVisibility="Disabled">
                                <StackPanel x:Name="Options"/>
                            </ScrollViewer>
                        </DockPanel>
                    </Border>
                </Grid>

                <Grid Grid.Row="2" Margin="0,14,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition/>
                        <ColumnDefinition Width="Auto"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Margin="0,0,14,0" VerticalAlignment="Center">
                        <TextBlock x:Name="StatusText" TextWrapping="Wrap" FontSize="12"
                                   Foreground="{DynamicResource Muted}"/>
                        <ProgressBar x:Name="Activity" Height="3" Margin="0,6,0,0" IsIndeterminate="True"
                                     Background="{DynamicResource Control}" Foreground="{DynamicResource Blue}"
                                     Visibility="Collapsed"/>
                    </StackPanel>
                    <Button x:Name="RunAction" Grid.Column="1" Style="{StaticResource PrimaryButton}"/>
                </Grid>
            </Grid>

            <ScrollViewer x:Name="SettingsView" Visibility="Collapsed" VerticalScrollBarVisibility="Auto"
                          HorizontalScrollBarVisibility="Disabled">
                <StackPanel MaxWidth="700" HorizontalAlignment="Left">
                    <TextBlock Text="Settings" FontSize="28" FontWeight="SemiBold" Margin="0,0,0,18"/>
                    <Border Style="{StaticResource Card}" Margin="0,0,0,14">
                        <StackPanel>
                            <TextBlock Text="Appearance" FontSize="18" FontWeight="SemiBold" Margin="0,0,0,12"/>
                            <StackPanel Orientation="Horizontal">
                                <RadioButton x:Name="LightTheme" Content="Light" GroupName="Theme"
                                             Style="{StaticResource PresetButton}" Tag="Light backgrounds" Width="165" Margin="0,0,10,0"/>
                                <RadioButton x:Name="DarkTheme" Content="Dark" GroupName="Theme"
                                             Style="{StaticResource PresetButton}" Tag="Dark backgrounds" Width="165" Margin="0"/>
                            </StackPanel>
                            <TextBlock x:Name="ThemeStatus" Foreground="{DynamicResource Muted}" TextWrapping="Wrap"
                                       FontSize="12" Margin="0,9,0,0"/>
                        </StackPanel>
                    </Border>
                    <Border Style="{StaticResource Card}" Margin="0,0,0,14">
                        <StackPanel>
                            <TextBlock Text="Updates" FontSize="18" FontWeight="SemiBold"/>
                            <TextBlock x:Name="VersionLabel" Foreground="{DynamicResource Muted}" Margin="0,6,0,14"/>
                            <WrapPanel>
                                <Button x:Name="CheckUpdates" Content="Check for Updates" Style="{StaticResource ButtonBase}"/>
                                <Button x:Name="UpdateAction" Style="{StaticResource PrimaryButton}"
                                        Visibility="Collapsed" Margin="8,0,0,0"/>
                            </WrapPanel>
                            <TextBlock x:Name="UpdateStatus" TextWrapping="Wrap" Margin="0,12,0,0"
                                       Foreground="{DynamicResource Muted}"/>
                        </StackPanel>
                    </Border>
                    <Border Style="{StaticResource Card}">
                        <StackPanel>
                            <TextBlock Text="Logs" FontSize="18" FontWeight="SemiBold"/>
                            <TextBox x:Name="LogPath" IsReadOnly="True" TextWrapping="Wrap" Margin="0,8,0,12"
                                     AutomationProperties.Name="Log file path"/>
                            <Button x:Name="OpenLogs" Content="Open Logs Folder" Style="{StaticResource ButtonBase}"
                                    HorizontalAlignment="Left" IsEnabled="False"/>
                            <TextBlock x:Name="LogStatus" TextWrapping="Wrap" Margin="0,9,0,0"
                                       Foreground="{DynamicResource Muted}"/>
                        </StackPanel>
                    </Border>
                </StackPanel>
            </ScrollViewer>
        </Grid>
    </Grid>
</Window>
"""


WINDOW_COMMANDS = r"""
$MinimizeWindow.Add_Click({ [Windows.SystemCommands]::MinimizeWindow($window) })
$ToggleWindowSize.Add_Click({
    if ($window.WindowState -eq 'Maximized') {
        [Windows.SystemCommands]::RestoreWindow($window)
    } else {
        [Windows.SystemCommands]::MaximizeWindow($window)
    }
})
$window.Add_StateChanged({
    $maximized = $window.WindowState -eq 'Maximized'
    $ToggleWindowSize.Content = if ($maximized) { [string][char]0xE923 } else { [string][char]0xE922 }
    $label = if ($maximized) { 'Restore' } else { 'Maximize' }
    $ToggleWindowSize.ToolTip = $label
    [Windows.Automation.AutomationProperties]::SetName($ToggleWindowSize, $label)
})
$CloseWindow.Add_Click({ $window.Close() })

"""


REORDER = r"""
function Move-File([int]$index, [int]$destination) {
    if (-not (Can-Reorder) -or $index -lt 0 -or $destination -lt 0 -or $destination -ge $ui.Active.Rows.Count) { return }
    if ($index -ne $destination) { $ui.Active.Rows.Move($index, $destination) }
    $FileList.SelectedIndex = $destination
    Update-OrderField
    Update-Controls
}

function Move-SelectedFile([int]$offset) {
    $index = $FileList.SelectedIndex
    Move-File $index ($index + $offset)
}

$drag = @{
    Start = $null
    Armed = $null
    Active = $false
    Hidden = $null
    Offsets = @{}
}
$dragEase = [Windows.Media.Animation.QuadraticEase]::new()
$dragEase.EasingMode = 'EaseOut'
$dragDuration = [Windows.Duration]::new([TimeSpan]::FromMilliseconds(145))
$dragScrollTimer = [Windows.Threading.DispatcherTimer]::new()
$dragScrollTimer.Interval = [TimeSpan]::FromMilliseconds(80)

function Set-RowOffset($container, [double]$offset) {
    if ($drag.Offsets.ContainsKey($container)) {
        if ($drag.Offsets[$container] -eq $offset) { return }
        $transform = $container.RenderTransform
    } else {
        if ($offset -eq 0) { return }
        $transform = [Windows.Media.TranslateTransform]::new()
        $container.RenderTransform = $transform
    }

    $from = $transform.Y
    $transform.BeginAnimation([Windows.Media.TranslateTransform]::YProperty, $null)
    $transform.Y = $offset
    $animation = [Windows.Media.Animation.DoubleAnimation]::new($from, $offset, $dragDuration)
    $animation.EasingFunction = $dragEase
    $animation.FillBehavior = 'Stop'
    $transform.BeginAnimation([Windows.Media.TranslateTransform]::YProperty, $animation)
    $drag.Offsets[$container] = $offset
}

function Update-RowDrag([Windows.Point]$point) {
    $drag.Point = $point
    $scroll = $drag.Scroll.VerticalOffset
    $floatingTop = $point.Y - $drag.GrabOffset
    $floatingLimit = [Math]::Max(0, $ReorderOverlay.ActualHeight - $FloatingRow.Height)
    [Windows.Controls.Canvas]::SetTop($FloatingRow, [Math]::Max(0, [Math]::Min($floatingTop, $floatingLimit)))

    # Calculate the slot in the original layout. Animated rows do not affect targeting.
    $nearest = [Math]::Floor(($floatingTop + $scroll - $drag.FirstRowTop) / $drag.Step + .5)
    $slot = [int][Math]::Max(0, [Math]::Min($ui.Active.Rows.Count - 1, $nearest))
    if ($drag.Slot -eq $slot -and $drag.ScrollOffset -eq $scroll) { return }
    $drag.Slot = $slot
    $drag.ScrollOffset = $scroll
    [Windows.Controls.Canvas]::SetTop($ReorderSlot, ($drag.FirstRowTop + $slot * $drag.Step - $scroll))

    $generator = $FileList.ItemContainerGenerator
    $source = $generator.ContainerFromIndex($drag.Source)
    if (-not [object]::ReferenceEquals($drag.Hidden, $source)) {
        if ($null -ne $drag.Hidden) { $drag.Hidden.ClearValue([Windows.UIElement]::OpacityProperty) }
        $drag.Hidden = $source
        if ($null -ne $source) { $source.Opacity = 0 }
    }

    $firstVisible = [int][Math]::Max(0, [Math]::Floor($scroll / $drag.Step) - 1)
    $lastVisible = [int][Math]::Min(
        $ui.Active.Rows.Count - 1, [Math]::Ceiling(($scroll + $FileList.ActualHeight) / $drag.Step) + 1
    )
    for ($index = $firstVisible; $index -le $lastVisible; $index++) {
        $container = $generator.ContainerFromIndex($index)
        if ($null -eq $container) { continue }
        $offset = 0
        if ($index -gt $drag.Source -and $index -le $slot) {
            $offset = -$drag.Step
        } elseif ($index -ge $slot -and $index -lt $drag.Source) {
            $offset = $drag.Step
        }
        Set-RowOffset $container $offset
    }
}

function Start-RowDrag($container, [Windows.Point]$press) {
    $origin = $container.TranslatePoint([Windows.Point]::new(0, 0), $FileList)
    $drag.Active = $true
    $drag.Source = $FileList.ItemContainerGenerator.IndexFromContainer($container)
    $drag.Start = $null
    $drag.Armed = $null
    $drag.Slot = -1
    $drag.Scroll = $FileList.Template.FindName('QueueScroll', $FileList)
    $drag.Step = $container.ActualHeight + $container.Margin.Top + $container.Margin.Bottom
    $drag.FirstRowTop = $origin.Y + $drag.Scroll.VerticalOffset - $drag.Source * $drag.Step
    $drag.GrabOffset = $press.Y - $origin.Y
    $FileList.SelectedIndex = $drag.Source

    $FloatingContent.Content = $container.Content
    $FloatingRow.Width = $ReorderSlot.Width = $container.ActualWidth
    $FloatingRow.Height = $ReorderSlot.Height = $container.ActualHeight
    [Windows.Controls.Canvas]::SetLeft($ReorderSlot, $origin.X)
    [Windows.Controls.Canvas]::SetLeft($FloatingRow, $origin.X)
    $ReorderOverlay.Visibility = 'Visible'
    $ReorderOverlay.UpdateLayout()
    Update-RowDrag $press
    $dragScrollTimer.Start()
}

function End-RowDrag([bool]$commit) {
    $drag.Start = $null
    $drag.Armed = $null
    if (-not $drag.Active) { return }
    $drag.Active = $false
    $dragScrollTimer.Stop()
    $ReorderOverlay.Visibility = 'Collapsed'
    $FloatingContent.Content = $null
    if ($null -ne $drag.Hidden) {
        $drag.Hidden.ClearValue([Windows.UIElement]::OpacityProperty)
        $drag.Hidden = $null
    }
    foreach ($container in $drag.Offsets.Keys) {
        $container.ClearValue([Windows.UIElement]::RenderTransformProperty)
    }
    $drag.Offsets.Clear()
    if ($commit) { Move-File $drag.Source $drag.Slot }
    if ([object]::ReferenceEquals([Windows.Input.Mouse]::Captured, $FileList)) {
        $FileList.ReleaseMouseCapture()
    }
}

$dragScrollTimer.Add_Tick({
    $point = $drag.Point
    if (-not $drag.Active -or $point.X -lt 0 -or $point.X -gt $FileList.ActualWidth) { return }
    $scroll = $drag.Scroll
    $direction = 0
    if ($point.Y -lt 24) {
        $direction = -1
    } elseif ($point.Y -gt ($scroll.ActualHeight - 24)) {
        $direction = 1
    }
    $offset = [Math]::Max(0, [Math]::Min($scroll.ScrollableHeight, $scroll.VerticalOffset + $direction * $drag.Step))
    if ($offset -eq $scroll.VerticalOffset) { return }
    $scroll.ScrollToVerticalOffset($offset)
    $FileList.UpdateLayout()
    Update-RowDrag $point
})

$FileList.Add_PreviewMouseLeftButtonDown({
    param($sender, $eventArgs)
    $drag.Start = $null
    $drag.Armed = $null
    $container = [Windows.Controls.ItemsControl]::ContainerFromElement($sender, $eventArgs.OriginalSource)
    if ($null -ne $container -and (Can-Reorder) -and $ui.Active.Rows.Count -gt 1) {
        $drag.Start = $eventArgs.GetPosition($sender)
        $drag.Armed = $container
    }
})
$FileList.Add_PreviewMouseMove({
    param($sender, $eventArgs)
    if ($eventArgs.LeftButton -ne 'Pressed') { return }
    $point = $eventArgs.GetPosition($sender)

    if (-not $drag.Active) {
        if ($null -eq $drag.Start) { return }
        if ([Math]::Abs($point.X - $drag.Start.X) -lt [Windows.SystemParameters]::MinimumHorizontalDragDistance -and
            [Math]::Abs($point.Y - $drag.Start.Y) -lt [Windows.SystemParameters]::MinimumVerticalDragDistance) { return }
        $container = $drag.Armed
        $press = $drag.Start
        if (-not [Windows.Input.Mouse]::Capture($sender, 'SubTree')) { return }
        Start-RowDrag $container $press
    }
    Update-RowDrag $point
    $eventArgs.Handled = $true
})
$FileList.Add_PreviewMouseLeftButtonUp({
    param($sender, $eventArgs)
    if (-not $drag.Active) {
        End-RowDrag $false
        return
    }
    $point = $eventArgs.GetPosition($sender)
    $inside = $point.X -ge 0 -and $point.X -le $sender.ActualWidth -and
              $point.Y -ge 0 -and $point.Y -le $sender.ActualHeight
    if ($inside) { Update-RowDrag $point }
    End-RowDrag $inside
    $eventArgs.Handled = $true
})
$FileList.Add_LostMouseCapture({
    param($sender, $eventArgs)
    if ([object]::ReferenceEquals($eventArgs.OriginalSource, $sender)) { End-RowDrag $false }
})
$window.Add_PreviewKeyDown({
    param($sender, $eventArgs)
    if ($drag.Active -and $eventArgs.Key -eq 'Escape') {
        End-RowDrag $false
        $eventArgs.Handled = $true
    }
})
$window.Add_Deactivated({ End-RowDrag $false })

"""
