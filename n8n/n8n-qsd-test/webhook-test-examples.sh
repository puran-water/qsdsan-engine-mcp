#!/bin/bash
# QSDsan Simulation v9 - Webhook Test Examples (Bash/Curl)
# =========================================================
#
# This script contains curl examples for triggering the v9 workflow via webhook.
#
# Usage:
#   1. Update the WEBHOOK_URL variable below with your n8n webhook URL
#   2. Make script executable: chmod +x webhook-test-examples.sh
#   3. Run: ./webhook-test-examples.sh
#   4. Select an example from the menu
#
# For testing: Use webhook-test URL (workflow doesn't need to be active)
# For production: Use webhook URL (workflow must be active)

# =============================================================================
# CONFIGURATION - Update with your n8n instance URL
# =============================================================================

WEBHOOK_URL="https://n8n.panicle.org/webhook/qsdsan-simulate"
WEBHOOK_TEST_URL="https://n8n.panicle.org/webhook-test/qsdsan-simulate"

# Use test URL by default
BASE_URL="$WEBHOOK_TEST_URL"

# =============================================================================
# COLOR CODES
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# =============================================================================
# EXAMPLES
# =============================================================================

# Example 1: Study Mode - Basic
example1() {
    echo -e "${GREEN}Running: Study Mode - Dairy Baseline${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "study_id": "dairy_baseline"
        }' | jq .
}

# Example 2: Study Mode - With Overrides
example2() {
    echo -e "${GREEN}Running: Study Mode - With Overrides${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "study_id": "dairy_baseline",
            "overrides": {
                "influent": {
                    "flow_m3_d": 1500,
                    "simplified": {
                        "COD_mg_L": 6000
                    }
                }
            }
        }' | jq .
}

# Example 3: Study Mode - Brewery
example3() {
    echo -e "${GREEN}Running: Study Mode - Brewery${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "study_id": "brewery_baseline"
        }' | jq .
}

# Example 4: Study Mode - Anaerobic (mADM1)
example4() {
    echo -e "${GREEN}Running: Study Mode - Dairy Anaerobic (mADM1)${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "study_id": "dairy_anaerobic"
        }' | jq .
}

# Example 5: Direct Mode - Full ASM2d Configuration
example5() {
    echo -e "${GREEN}Running: Direct Mode - ASM2d Basic${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "simulation": {
                "template": "mle_mbr_asm2d",
                "model_type": "ASM2d",
                "timeout_seconds": 300,
                "duration_days": 1.0
            },
            "influent": {
                "flow_m3_d": 4000,
                "simplified": {
                    "COD_mg_L": 350,
                    "NH4_mg_L": 25,
                    "TP_mg_L": 8,
                    "TSS_mg_L": 220,
                    "temperature_C": 20
                }
            }
        }' | jq .
}

# Example 6: Direct Mode - With Convergence
example6() {
    echo -e "${GREEN}Running: Direct Mode - With Convergence${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "simulation": {
                "template": "mle_mbr_asm2d",
                "model_type": "ASM2d",
                "timeout_seconds": 600
            },
            "influent": {
                "flow_m3_d": 4000,
                "simplified": {
                    "COD_mg_L": 500,
                    "NH4_mg_L": 40,
                    "TP_mg_L": 10,
                    "TSS_mg_L": 300,
                    "temperature_C": 18
                }
            },
            "convergence": {
                "run_to_convergence": true,
                "convergence_atol": 0.1,
                "max_duration_days": 60
            }
        }' | jq .
}

# Example 7: Direct Mode - With SRT Control
example7() {
    echo -e "${GREEN}Running: Direct Mode - With SRT Control${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "simulation": {
                "template": "mle_mbr_asm2d",
                "model_type": "ASM2d",
                "timeout_seconds": 900
            },
            "influent": {
                "flow_m3_d": 4000,
                "simplified": {
                    "COD_mg_L": 400,
                    "NH4_mg_L": 30,
                    "TP_mg_L": 8,
                    "TSS_mg_L": 250,
                    "temperature_C": 20
                }
            },
            "convergence": {
                "run_to_convergence": true
            },
            "srt_control": {
                "target_srt_days": 15,
                "srt_tolerance": 0.1
            }
        }' | jq .
}

# Example 8: Legacy Mode - v8 Compatible
example8() {
    echo -e "${GREEN}Running: Legacy Mode - v8 Compatible${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "template": "mle_mbr_asm2d",
            "flow_m3_d": 4000,
            "COD_mg_L": 350,
            "NH4_mg_L": 25,
            "TP_mg_L": 8,
            "TSS_mg_L": 220,
            "temperature_C": 20,
            "timeout_seconds": 300
        }' | jq .
}

# Example 9: Legacy Mode - Explicit Flag
example9() {
    echo -e "${GREEN}Running: Legacy Mode - Explicit Flag (A/O MBR)${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "legacy_mode": true,
            "template": "ao_mbr_asm2d",
            "flow_m3_d": 3000,
            "COD_mg_L": 400,
            "NH4_mg_L": 35,
            "TP_mg_L": 10,
            "TSS_mg_L": 280,
            "temperature_C": 22
        }' | jq .
}

# Example 10: Direct Mode - High-Strength Industrial
example10() {
    echo -e "${GREEN}Running: Direct Mode - High-Strength Industrial${NC}"
    curl -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d '{
            "simulation": {
                "template": "mle_mbr_asm2d",
                "model_type": "ASM2d",
                "timeout_seconds": 600,
                "duration_days": 2.0
            },
            "influent": {
                "flow_m3_d": 2000,
                "simplified": {
                    "COD_mg_L": 2500,
                    "NH4_mg_L": 80,
                    "TP_mg_L": 25,
                    "TSS_mg_L": 800,
                    "temperature_C": 25,
                    "cod_distribution": {
                        "f_soluble": 0.4,
                        "f_fermentable": 0.6,
                        "f_acetate": 0.2
                    }
                }
            }
        }' | jq .
}

# =============================================================================
# MENU
# =============================================================================

show_menu() {
    echo ""
    echo -e "${CYAN}=============================================${NC}"
    echo -e "${CYAN}QSDsan v9 Webhook Test Script${NC}"
    echo -e "${CYAN}=============================================${NC}"
    echo -e "${YELLOW}Current URL: $BASE_URL${NC}"
    echo ""
    echo "Select an example to run:"
    echo ""
    echo "  STUDY MODE (fetch from Supabase):"
    echo "    1. Dairy Baseline"
    echo "    2. Dairy with Overrides"
    echo "    3. Brewery Baseline"
    echo "    4. Dairy Anaerobic (mADM1)"
    echo ""
    echo "  DIRECT MODE (full configuration):"
    echo "    5. ASM2d Basic"
    echo "    6. With Convergence"
    echo "    7. With SRT Control"
    echo "    10. High-Strength Industrial"
    echo ""
    echo "  LEGACY MODE (v8 compatible):"
    echo "    8. v8 Compatible (flat params)"
    echo "    9. Explicit Flag (A/O MBR)"
    echo ""
    echo "  OPTIONS:"
    echo "    t. Toggle Test/Production URL"
    echo "    q. Quit"
    echo ""
}

toggle_url() {
    if [ "$BASE_URL" = "$WEBHOOK_TEST_URL" ]; then
        BASE_URL="$WEBHOOK_URL"
        echo -e "${YELLOW}Switched to Production URL: $BASE_URL${NC}"
    else
        BASE_URL="$WEBHOOK_TEST_URL"
        echo -e "${YELLOW}Switched to Test URL: $BASE_URL${NC}"
    fi
}

# =============================================================================
# MAIN
# =============================================================================

while true; do
    show_menu
    read -p "Enter choice: " choice

    case $choice in
        1) example1 ;;
        2) example2 ;;
        3) example3 ;;
        4) example4 ;;
        5) example5 ;;
        6) example6 ;;
        7) example7 ;;
        8) example8 ;;
        9) example9 ;;
        10) example10 ;;
        t|T) toggle_url ;;
        q|Q) echo "Goodbye!"; exit 0 ;;
        *) echo -e "${RED}Invalid choice${NC}" ;;
    esac

    echo ""
    read -p "Press Enter to continue..."
done
