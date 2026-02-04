# QSDsan Simulation v9 - Webhook Test Examples
# =============================================
#
# This script contains examples for triggering the v9 workflow via webhook.
#
# Usage:
#   1. Update the $WebhookUrl variable below with your n8n webhook URL
#   2. Run individual examples or the entire script
#   3. For testing, use the webhook-test URL (workflow doesn't need to be active)
#   4. For production, use the webhook URL (workflow must be active)
#
# Note: Run this script in PowerShell

# =============================================================================
# CONFIGURATION - Update this with your n8n instance URL
# =============================================================================

$WebhookUrl = "https://n8n.panicle.org/webhook/qsdsan-simulate"        # Production (active workflow)
$WebhookTestUrl = "https://n8n.panicle.org/webhook-test/qsdsan-simulate"  # Testing (listen mode)

# Use test URL by default
$BaseUrl = $WebhookTestUrl

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "QSDsan v9 Webhook Test Script" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Using URL: $BaseUrl" -ForegroundColor Yellow
Write-Host ""

# =============================================================================
# HELPER FUNCTION
# =============================================================================

function Invoke-QSDsanWebhook {
    param(
        [string]$Name,
        [string]$Description,
        [hashtable]$Body
    )

    Write-Host "---------------------------------------------" -ForegroundColor Gray
    Write-Host "Test: $Name" -ForegroundColor Green
    Write-Host "Description: $Description" -ForegroundColor White
    Write-Host ""

    $JsonBody = $Body | ConvertTo-Json -Depth 10
    Write-Host "Request Body:" -ForegroundColor Yellow
    Write-Host $JsonBody
    Write-Host ""

    try {
        $Response = Invoke-RestMethod -Uri $BaseUrl `
            -Method POST `
            -ContentType "application/json" `
            -Body $JsonBody `
            -TimeoutSec 600

        Write-Host "Response:" -ForegroundColor Green
        $Response | ConvertTo-Json -Depth 5 | Write-Host
    }
    catch {
        Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
        if ($_.ErrorDetails.Message) {
            Write-Host "Details: $($_.ErrorDetails.Message)" -ForegroundColor Red
        }
    }
    Write-Host ""
}

# =============================================================================
# EXAMPLE 1: STUDY MODE - Basic
# =============================================================================

$Example1 = @{
    study_id = "dairy_baseline"
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Study Mode - Basic" -Description "Fetch and run the dairy_baseline study from Supabase" -Body $Example1

# =============================================================================
# EXAMPLE 2: STUDY MODE - With Overrides
# =============================================================================

$Example2 = @{
    study_id = "dairy_baseline"
    overrides = @{
        influent = @{
            flow_m3_d = 1500
            simplified = @{
                COD_mg_L = 6000
            }
        }
    }
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Study Mode - With Overrides" -Description "Run dairy study with modified flow and COD" -Body $Example2

# =============================================================================
# EXAMPLE 3: STUDY MODE - Brewery
# =============================================================================

$Example3 = @{
    study_id = "brewery_baseline"
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Study Mode - Brewery" -Description "Run the brewery baseline study" -Body $Example3

# =============================================================================
# EXAMPLE 4: STUDY MODE - Anaerobic (mADM1)
# =============================================================================

$Example4 = @{
    study_id = "dairy_anaerobic"
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Study Mode - Anaerobic" -Description "Run dairy anaerobic treatment (mADM1 model)" -Body $Example4

# =============================================================================
# EXAMPLE 5: DIRECT MODE - Full ASM2d Configuration
# =============================================================================

$Example5 = @{
    simulation = @{
        template = "mle_mbr_asm2d"
        model_type = "ASM2d"
        timeout_seconds = 300
        duration_days = 1.0
    }
    influent = @{
        flow_m3_d = 4000
        simplified = @{
            COD_mg_L = 350
            NH4_mg_L = 25
            TP_mg_L = 8
            TSS_mg_L = 220
            temperature_C = 20
        }
    }
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Direct Mode - ASM2d" -Description "Full configuration for MLE MBR with ASM2d" -Body $Example5

# =============================================================================
# EXAMPLE 6: DIRECT MODE - With Convergence
# =============================================================================

$Example6 = @{
    simulation = @{
        template = "mle_mbr_asm2d"
        model_type = "ASM2d"
        timeout_seconds = 600
    }
    influent = @{
        flow_m3_d = 4000
        simplified = @{
            COD_mg_L = 500
            NH4_mg_L = 40
            TP_mg_L = 10
            TSS_mg_L = 300
            temperature_C = 18
        }
    }
    convergence = @{
        run_to_convergence = $true
        convergence_atol = 0.1
        max_duration_days = 60
    }
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Direct Mode - With Convergence" -Description "Run to steady-state convergence" -Body $Example6

# =============================================================================
# EXAMPLE 7: DIRECT MODE - With SRT Control
# =============================================================================

$Example7 = @{
    simulation = @{
        template = "mle_mbr_asm2d"
        model_type = "ASM2d"
        timeout_seconds = 900
    }
    influent = @{
        flow_m3_d = 4000
        simplified = @{
            COD_mg_L = 400
            NH4_mg_L = 30
            TP_mg_L = 8
            TSS_mg_L = 250
            temperature_C = 20
        }
    }
    convergence = @{
        run_to_convergence = $true
    }
    srt_control = @{
        target_srt_days = 15
        srt_tolerance = 0.1
    }
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Direct Mode - SRT Control" -Description "Target 15-day SRT with convergence" -Body $Example7

# =============================================================================
# EXAMPLE 8: LEGACY MODE - v8 Compatible (Flat Parameters)
# =============================================================================

$Example8 = @{
    template = "mle_mbr_asm2d"
    flow_m3_d = 4000
    COD_mg_L = 350
    NH4_mg_L = 25
    TP_mg_L = 8
    TSS_mg_L = 220
    temperature_C = 20
    timeout_seconds = 300
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Legacy Mode - v8 Compatible" -Description "Flat parameter format (backward compatible with v8)" -Body $Example8

# =============================================================================
# EXAMPLE 9: LEGACY MODE - Explicit Flag
# =============================================================================

$Example9 = @{
    legacy_mode = $true
    template = "ao_mbr_asm2d"
    flow_m3_d = 3000
    COD_mg_L = 400
    NH4_mg_L = 35
    TP_mg_L = 10
    TSS_mg_L = 280
    temperature_C = 22
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Legacy Mode - Explicit Flag" -Description "A/O MBR template with explicit legacy_mode flag" -Body $Example9

# =============================================================================
# EXAMPLE 10: DIRECT MODE - High-Strength Industrial Wastewater
# =============================================================================

$Example10 = @{
    simulation = @{
        template = "mle_mbr_asm2d"
        model_type = "ASM2d"
        timeout_seconds = 600
        duration_days = 2.0
    }
    influent = @{
        flow_m3_d = 2000
        simplified = @{
            COD_mg_L = 2500
            NH4_mg_L = 80
            TP_mg_L = 25
            TSS_mg_L = 800
            temperature_C = 25
            cod_distribution = @{
                f_soluble = 0.4
                f_fermentable = 0.6
                f_acetate = 0.2
            }
        }
    }
}

# Uncomment to run:
# Invoke-QSDsanWebhook -Name "Direct Mode - High-Strength Industrial" -Description "High-strength wastewater with custom COD distribution" -Body $Example10

# =============================================================================
# INTERACTIVE MENU
# =============================================================================

function Show-Menu {
    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Cyan
    Write-Host "Select an example to run:" -ForegroundColor Cyan
    Write-Host "=============================================" -ForegroundColor Cyan
    Write-Host "1. Study Mode - Dairy Baseline"
    Write-Host "2. Study Mode - Dairy with Overrides"
    Write-Host "3. Study Mode - Brewery Baseline"
    Write-Host "4. Study Mode - Dairy Anaerobic (mADM1)"
    Write-Host "5. Direct Mode - ASM2d Basic"
    Write-Host "6. Direct Mode - With Convergence"
    Write-Host "7. Direct Mode - With SRT Control"
    Write-Host "8. Legacy Mode - v8 Compatible"
    Write-Host "9. Legacy Mode - Explicit Flag"
    Write-Host "10. Direct Mode - High-Strength Industrial"
    Write-Host ""
    Write-Host "T. Toggle between Test/Production URL"
    Write-Host "Q. Quit"
    Write-Host ""
}

function Run-Interactive {
    $running = $true

    while ($running) {
        Show-Menu
        $choice = Read-Host "Enter choice"

        switch ($choice) {
            "1" { Invoke-QSDsanWebhook -Name "Study Mode - Dairy Baseline" -Description "Fetch and run dairy_baseline from Supabase" -Body $Example1 }
            "2" { Invoke-QSDsanWebhook -Name "Study Mode - With Overrides" -Description "Dairy study with modified parameters" -Body $Example2 }
            "3" { Invoke-QSDsanWebhook -Name "Study Mode - Brewery" -Description "Run brewery baseline study" -Body $Example3 }
            "4" { Invoke-QSDsanWebhook -Name "Study Mode - Anaerobic" -Description "Dairy anaerobic (mADM1)" -Body $Example4 }
            "5" { Invoke-QSDsanWebhook -Name "Direct Mode - ASM2d" -Description "Full ASM2d configuration" -Body $Example5 }
            "6" { Invoke-QSDsanWebhook -Name "Direct Mode - Convergence" -Description "Run to steady-state" -Body $Example6 }
            "7" { Invoke-QSDsanWebhook -Name "Direct Mode - SRT Control" -Description "Target 15-day SRT" -Body $Example7 }
            "8" { Invoke-QSDsanWebhook -Name "Legacy Mode - v8" -Description "Flat parameters" -Body $Example8 }
            "9" { Invoke-QSDsanWebhook -Name "Legacy Mode - Explicit" -Description "A/O MBR template" -Body $Example9 }
            "10" { Invoke-QSDsanWebhook -Name "Direct Mode - Industrial" -Description "High-strength wastewater" -Body $Example10 }
            "T" {
                if ($script:BaseUrl -eq $WebhookTestUrl) {
                    $script:BaseUrl = $WebhookUrl
                    Write-Host "Switched to Production URL: $script:BaseUrl" -ForegroundColor Yellow
                } else {
                    $script:BaseUrl = $WebhookTestUrl
                    Write-Host "Switched to Test URL: $script:BaseUrl" -ForegroundColor Yellow
                }
            }
            "Q" { $running = $false }
            default { Write-Host "Invalid choice" -ForegroundColor Red }
        }
    }
}

# =============================================================================
# RUN INTERACTIVE MENU
# =============================================================================

Write-Host ""
Write-Host "Run interactive menu? (Y/N)" -ForegroundColor Cyan
$runInteractive = Read-Host

if ($runInteractive -eq "Y" -or $runInteractive -eq "y") {
    Run-Interactive
} else {
    Write-Host ""
    Write-Host "To run individual examples, uncomment the Invoke-QSDsanWebhook lines above"
    Write-Host "Or run: Run-Interactive"
    Write-Host ""
}
