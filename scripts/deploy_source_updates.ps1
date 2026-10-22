param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string]$ServerHost='123.56.222.21',
      [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$Release,
      [switch]$ArchiveEmptyPreviewTitleStop)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot 'predeploy.ps1') -ServerHost $ServerHost -Release $Release -InstallSourceUpdates -ArchiveEmptyPreviewTitleStop:$ArchiveEmptyPreviewTitleStop
