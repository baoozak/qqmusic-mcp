& {
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repository = "baoozak/qqmusic-mcp"
$releaseApi = "https://api.github.com/repos/$repository/releases/latest"
$allowedClients = @("codex", "claude", "cursor", "vscode")

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Download-File([string]$Url, [string]$Destination) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ("qqmusic-mcp-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporary | Out-Null

try {
    $uv = Find-Uv
    if (-not $uv) {
        Write-Host "Installing uv..."
        $uvInstaller = Join-Path $temporary "uv-installer.ps1"
        Download-File "https://astral.sh/uv/install.ps1" $uvInstaller
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $uvInstaller
        if ($LASTEXITCODE -ne 0) {
            throw "The official uv installer failed."
        }
        $uv = Find-Uv
        if (-not $uv) {
            throw "uv was installed but could not be located. Open a new terminal and run this installer again."
        }
    }

    Write-Host "Reading the latest qqmusic-mcp release..."
    $headers = @{ "User-Agent" = "qqmusic-mcp-installer" }
    $release = Invoke-RestMethod -UseBasicParsing -Uri $releaseApi -Headers $headers
    $wheelAssets = @($release.assets | Where-Object { $_.name -match '^qqmusic_mcp-[0-9A-Za-z_.+\-]+-py3-none-any\.whl$' })
    if ($wheelAssets.Count -ne 1) {
        throw "The latest release must contain exactly one qqmusic-mcp wheel."
    }
    $wheelAsset = $wheelAssets[0]
    $checksumName = $wheelAsset.name + ".sha256"
    $checksumAssets = @($release.assets | Where-Object { $_.name -eq $checksumName })
    if ($checksumAssets.Count -ne 1) {
        throw "The latest release does not contain $checksumName."
    }
    $expectedPrefix = "https://github.com/$repository/releases/download/"
    if (-not $wheelAsset.browser_download_url.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The release wheel URL is not hosted by the expected GitHub repository."
    }
    if (-not $checksumAssets[0].browser_download_url.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The checksum URL is not hosted by the expected GitHub repository."
    }

    $wheelPath = Join-Path $temporary $wheelAsset.name
    $checksumPath = Join-Path $temporary $checksumName
    Download-File $wheelAsset.browser_download_url $wheelPath
    Download-File $checksumAssets[0].browser_download_url $checksumPath
    $checksumText = (Get-Content -Raw -LiteralPath $checksumPath).Trim()
    $expectedHash = ($checksumText -split '\s+')[0].ToLowerInvariant()
    if ($expectedHash -notmatch '^[0-9a-f]{64}$') {
        throw "The release checksum has an invalid format."
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheelPath).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "The qqmusic-mcp wheel failed SHA-256 verification."
    }

    Write-Host "Installing qqmusic-mcp $($release.tag_name)..."
    & $uv tool install --force $wheelPath
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install qqmusic-mcp."
    }
    $toolBin = (& $uv tool dir --bin | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $toolBin) {
        throw "uv did not return its tool executable directory."
    }
    $qqmusicMcp = Join-Path $toolBin "qqmusic-mcp.exe"
    if (-not (Test-Path -LiteralPath $qqmusicMcp -PathType Leaf)) {
        throw "qqmusic-mcp was installed but its executable was not found."
    }

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathEntries = @($userPath -split ';' | Where-Object { $_ })
    if ($pathEntries -notcontains $toolBin) {
        $updatedPath = if ($userPath) { "$userPath;$toolBin" } else { $toolBin }
        [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
    }
    $env:Path = "$toolBin;$env:Path"

    $client = $env:QQMUSIC_MCP_CLIENT
    if (-not $client) {
        Write-Host "Supported clients: codex, claude, cursor, vscode"
        $client = Read-Host "Choose an MCP client [codex]"
        if (-not $client) {
            $client = "codex"
        }
    }
    $client = $client.ToLowerInvariant()
    if ($allowedClients -notcontains $client) {
        throw "Unsupported MCP client: $client"
    }

    Write-Host "Opening QQ Music login and configuring $client..."
    & $qqmusicMcp setup --client $client --login-timeout 600
    if ($LASTEXITCODE -ne 0) {
        throw "qqmusic-mcp was installed, but setup did not complete. Run 'qqmusic-mcp setup --client $client' to retry."
    }
    Write-Host "qqmusic-mcp is ready."
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
}
