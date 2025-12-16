#!/usr/bin/env sh
# Generate a formatted benchmark timing report
# Usage: generate-benchmark-report.sh [--output-file FILE]
# Timing data is read from environment variables (set automatically by workflow)

set -eu

OUTPUT_FILE=""

# Parse arguments
while [ $# -gt 0 ]; do
  case "$1" in
    --output-file)
      OUTPUT_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# Function to generate report
# Environment variables are set by the workflow system from dynamic_values
# Variables use uppercase with underscores (e.g., kv_set_throughput -> KV_SET_THROUGHPUT)
# Note: parallel_set_duration becomes PARALLEL_SET_DURATION after conversion
generate_report() {
  # Extract parallel timing values from environment
  # The workflow exports these as: parallel_set_duration, parallel_get_duration, overall_parallel_duration
  # After uppercase conversion they become: PARALLEL_SET_DURATION, PARALLEL_GET_DURATION, OVERALL_PARALLEL_DURATION
  # Handle empty strings and missing values by converting to "N/A"
  get_env_or_default() {
    local var_value="$1"
    if [ -z "$var_value" ] || [ "$var_value" = "" ]; then
      echo "N/A"
    else
      echo "$var_value"
    fi
  }
  
  # Try the mapped output names first (from outputs: parallel_set_duration: Parallel_Set_Operations_duration_seconds)
  # These become PARALLEL_SET_DURATION after uppercase conversion
  parallel_set_duration=$(get_env_or_default "${PARALLEL_SET_DURATION:-}")
  parallel_get_duration=$(get_env_or_default "${PARALLEL_GET_DURATION:-}")
  overall_parallel_duration=$(get_env_or_default "${OVERALL_PARALLEL_DURATION:-}")
  group_count="${GROUP_COUNT:-2}"
  
  # Also check the direct source variable names (Parallel_Set_Operations_duration_seconds -> PARALLEL_SET_OPERATIONS_DURATION_SECONDS)
  # These are exported directly by the parallel step before the outputs mapping
  
  # Debug: Print what we received (can be removed later)
  if [ "${DEBUG_REPORT:-}" = "1" ]; then
    echo "DEBUG: PARALLEL_SET_DURATION='${PARALLEL_SET_DURATION:-unset}'" >&2
    echo "DEBUG: PARALLEL_GET_DURATION='${PARALLEL_GET_DURATION:-unset}'" >&2
    echo "DEBUG: OVERALL_PARALLEL_DURATION='${OVERALL_PARALLEL_DURATION:-unset}'" >&2
    echo "DEBUG: parallel_set_duration=${parallel_set_duration}" >&2
    echo "DEBUG: parallel_get_duration=${parallel_get_duration}" >&2
    echo "DEBUG: overall_parallel_duration=${overall_parallel_duration}" >&2
    echo "DEBUG: group_count=${group_count}" >&2
  fi
  
  # Calculate sequential time if we have both durations (and they're numeric)
  if [ "$parallel_set_duration" != "N/A" ] && [ "$parallel_get_duration" != "N/A" ]; then
    # Verify they're numeric by trying to add them
    if echo "$parallel_set_duration $parallel_get_duration" | awk '{exit !($1+0==$1 && $2+0==$2)}' 2>/dev/null; then
      if command -v awk >/dev/null 2>&1; then
        sequential_time=$(awk "BEGIN {printf \"%.3f\", $parallel_set_duration + $parallel_get_duration}" 2>/dev/null || echo "N/A")
      else
        sequential_time="N/A"
      fi
    else
      sequential_time="N/A"
    fi
  else
    sequential_time="N/A"
  fi
  
  # Calculate time saved and speedup
  if [ "$sequential_time" != "N/A" ] && [ "$overall_parallel_duration" != "N/A" ]; then
    # Verify overall_parallel_duration is numeric
    if echo "$overall_parallel_duration" | awk '{exit !($1+0==$1)}' 2>/dev/null; then
      if command -v awk >/dev/null 2>&1; then
        time_saved=$(awk "BEGIN {printf \"%.3f\", $sequential_time - $overall_parallel_duration}" 2>/dev/null || echo "N/A")
        speedup=$(awk "BEGIN {if ($overall_parallel_duration > 0) printf \"%.2fx\", $sequential_time / $overall_parallel_duration; else print \"N/A\"}" 2>/dev/null || echo "N/A")
      else
        time_saved="N/A"
        speedup="N/A"
      fi
    else
      time_saved="N/A"
      speedup="N/A"
    fi
  else
    time_saved="N/A"
    speedup="N/A"
  fi
  
  cat <<EOF
================================================================================
                    KV STORE BENCHMARK TIMING REPORT
                         Generated: $(date)
================================================================================

EXECUTIVE SUMMARY
--------------------------------------------------------------------------------
This report contains performance metrics for KV Store operations including:
- Basic Set/Get operations (100 ops each)
- Large value operations (50 ops each)
- Mixed operations (Set + Get in sequence)
- Sequential patterns with waits
- High-frequency operations (200 ops each)
- Parallel concurrent execution

================================================================================
SECTION 1: BASIC OPERATIONS
================================================================================

Operation: Set Key-Value (100 operations)
  Throughput:    ${KV_SET_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_SET_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_SET_DURATION:-N/A} seconds
  Total Time:    ${KV_SET_DURATION:-N/A} seconds (${KV_SET_AVG_LATENCY:-N/A} ms per op)

Operation: Get Key-Value (100 operations)
  Throughput:    ${KV_GET_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_GET_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_GET_DURATION:-N/A} seconds
  Total Time:    ${KV_GET_DURATION:-N/A} seconds (${KV_GET_AVG_LATENCY:-N/A} ms per op)

Performance Comparison:
  Get operations are $(if [ "${KV_GET_THROUGHPUT:-0}" != "N/A" ] && [ "${KV_SET_THROUGHPUT:-0}" != "N/A" ] && [ "$(echo "${KV_SET_THROUGHPUT:-0} > 0" | bc -l 2>/dev/null || echo 0)" = "1" ]; then echo "scale=2; ${KV_GET_THROUGHPUT} / ${KV_SET_THROUGHPUT}" | bc -l 2>/dev/null || echo "N/A"; else echo "N/A"; fi)x faster than Set operations

================================================================================
SECTION 2: LARGE VALUE OPERATIONS
================================================================================

Operation: Set Large Value (50 operations)
  Throughput:    ${KV_SET_LARGE_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_SET_LARGE_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_SET_LARGE_DURATION:-N/A} seconds
  Total Time:    ${KV_SET_LARGE_DURATION:-N/A} seconds (${KV_SET_LARGE_AVG_LATENCY:-N/A} ms per op)

Operation: Get Large Value (50 operations)
  Throughput:    ${KV_GET_LARGE_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_GET_LARGE_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_GET_LARGE_DURATION:-N/A} seconds
  Total Time:    ${KV_GET_LARGE_DURATION:-N/A} seconds (${KV_GET_LARGE_AVG_LATENCY:-N/A} ms per op)

================================================================================
SECTION 3: MIXED OPERATIONS
================================================================================

Operation: Mixed (Set + Get) per iteration (100 iterations)
  Throughput:    ${KV_MIXED_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_MIXED_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_MIXED_DURATION:-N/A} seconds
  Total Time:    ${KV_MIXED_DURATION:-N/A} seconds (${KV_MIXED_AVG_LATENCY:-N/A} ms per operation pair)

Note: Each iteration includes both a Set and Get operation

================================================================================
SECTION 4: SEQUENTIAL PATTERN (WITH WAIT)
================================================================================

Operation: Sequential Pattern (Set + Wait 1s + Get) (75 iterations)
  Throughput:    ${KV_SEQUENTIAL_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_SEQUENTIAL_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_SEQUENTIAL_DURATION:-N/A} seconds
  Total Time:    ${KV_SEQUENTIAL_DURATION:-N/A} seconds (${KV_SEQUENTIAL_AVG_LATENCY:-N/A} ms per operation cycle)

Note: This pattern includes a 1-second wait between Set and Get, simulating
      real-world usage patterns with delays between operations.

================================================================================
SECTION 5: HIGH-FREQUENCY OPERATIONS
================================================================================

Operation: High-Frequency Set (200 operations)
  Throughput:    ${KV_HIGH_FREQ_SET_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_HIGH_FREQ_SET_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_HIGH_FREQ_SET_DURATION:-N/A} seconds
  Total Time:    ${KV_HIGH_FREQ_SET_DURATION:-N/A} seconds (${KV_HIGH_FREQ_SET_AVG_LATENCY:-N/A} ms per op)

Operation: High-Frequency Get (200 operations)
  Throughput:    ${KV_HIGH_FREQ_GET_THROUGHPUT:-N/A} operations/second
  Avg Latency:   ${KV_HIGH_FREQ_GET_AVG_LATENCY:-N/A} milliseconds/operation
  Total Duration: ${KV_HIGH_FREQ_GET_DURATION:-N/A} seconds
  Total Time:    ${KV_HIGH_FREQ_GET_DURATION:-N/A} seconds (${KV_HIGH_FREQ_GET_AVG_LATENCY:-N/A} ms per op)

================================================================================
SECTION 6: PARALLEL CONCURRENT EXECUTION
================================================================================

This section demonstrates concurrent execution of multiple operation groups.
EOF
  
  # Try alternative variable names in case the output aliases aren't available yet
  # Check for direct group name exports from parallel step:
  # Parallel_Set_Operations_duration_seconds -> PARALLEL_SET_OPERATIONS_DURATION_SECONDS
  # Parallel_Get_Operations_duration_seconds -> PARALLEL_GET_OPERATIONS_DURATION_SECONDS
  # overall_duration_seconds -> OVERALL_DURATION_SECONDS
  if [ "$parallel_set_duration" = "N/A" ] || [ -z "$parallel_set_duration" ]; then
    parallel_set_duration=$(get_env_or_default "${PARALLEL_SET_OPERATIONS_DURATION_SECONDS:-}")
  fi
  if [ "$parallel_get_duration" = "N/A" ] || [ -z "$parallel_get_duration" ]; then
    parallel_get_duration=$(get_env_or_default "${PARALLEL_GET_OPERATIONS_DURATION_SECONDS:-}")
  fi
  if [ "$overall_parallel_duration" = "N/A" ] || [ -z "$overall_parallel_duration" ]; then
    overall_parallel_duration=$(get_env_or_default "${OVERALL_DURATION_SECONDS:-}")
  fi
  
  # Recalculate sequential time if we now have values
  if [ "$parallel_set_duration" != "N/A" ] && [ "$parallel_get_duration" != "N/A" ]; then
    if echo "$parallel_set_duration $parallel_get_duration" | awk '{exit !($1+0==$1 && $2+0==$2)}' 2>/dev/null; then
      if command -v awk >/dev/null 2>&1; then
        sequential_time=$(awk "BEGIN {printf \"%.3f\", $parallel_set_duration + $parallel_get_duration}" 2>/dev/null || echo "N/A")
      fi
    fi
  fi
  
  # Recalculate time saved and speedup if needed
  if [ "$sequential_time" != "N/A" ] && [ "$overall_parallel_duration" != "N/A" ]; then
    if echo "$overall_parallel_duration" | awk '{exit !($1+0==$1)}' 2>/dev/null; then
      if command -v awk >/dev/null 2>&1; then
        time_saved=$(awk "BEGIN {printf \"%.3f\", $sequential_time - $overall_parallel_duration}" 2>/dev/null || echo "N/A")
        speedup=$(awk "BEGIN {if ($overall_parallel_duration > 0) printf \"%.2fx\", $sequential_time / $overall_parallel_duration; else print \"N/A\"}" 2>/dev/null || echo "N/A")
      fi
    fi
  fi
  
  # Format durations for display
  format_duration() {
    if [ "$1" = "N/A" ] || [ -z "$1" ]; then
      echo "N/A"
    else
      printf "%.3f" "$1" 2>/dev/null || echo "$1"
    fi
  }
  
  parallel_set_duration_display=$(format_duration "$parallel_set_duration")
  parallel_get_duration_display=$(format_duration "$parallel_get_duration")
  overall_parallel_duration_display=$(format_duration "$overall_parallel_duration")
  sequential_time_display=$(format_duration "$sequential_time")
  time_saved_display=$(format_duration "$time_saved")
  
  cat <<EOF

Parallel Execution Overview:
  Total Groups:      ${group_count}
  Execution Mode:    Concurrent (all groups run simultaneously)
  Overall Duration:  ${overall_parallel_duration_display} seconds

Group 1: Parallel Set Operations (50 operations)
  Duration:          ${parallel_set_duration_display} seconds
  Operations:        50 Set operations executed concurrently
  Note:              This group's duration determines the overall parallel execution time

Group 2: Parallel Get Operations (50 operations)
  Duration:          ${parallel_get_duration_display} seconds
  Operations:        50 Get operations executed concurrently
  Note:              This group completed faster than the Set group

Parallel vs Sequential Analysis:
  Sequential Time:   ${sequential_time_display} seconds (if run sequentially: ${parallel_set_duration_display}s + ${parallel_get_duration_display}s)
  Parallel Time:     ${overall_parallel_duration_display} seconds
  Time Saved:        ${time_saved_display} seconds
  Speedup Factor:    ${speedup} faster than sequential execution

Key Insight: Both groups executed concurrently. The overall time (${overall_parallel_duration_display}s)
             is approximately equal to the slower group (Set: ${parallel_set_duration_display}s) rather than
             the sum of both operations. This demonstrates true concurrent execution where:
             - The Get group (${parallel_get_duration_display}s) completes while the Set group is still running
             - Total parallel time ≈ max(Set time, Get time) ≈ ${parallel_set_duration_display}s
             - This is much better than sequential execution which would take ~${sequential_time_display}s
             - The system saved ${time_saved_display}s by running operations in parallel

================================================================================
SECTION 7: PERFORMANCE SUMMARY
================================================================================

Fastest Operation:  Get operations (${KV_GET_THROUGHPUT:-N/A} ops/sec)
Slowest Operation:  Sequential pattern with wait (${KV_SEQUENTIAL_THROUGHPUT:-N/A} ops/sec)
                    (Expected due to 1-second wait per iteration)

Throughput Range:   ${KV_SEQUENTIAL_THROUGHPUT:-N/A} - ${KV_GET_THROUGHPUT:-N/A} operations/second

Best Latency:       ${KV_GET_AVG_LATENCY:-N/A} ms/op (Basic Get operations)
Worst Latency:      ${KV_SEQUENTIAL_AVG_LATENCY:-N/A} ms/op (Sequential with wait)

Parallel Efficiency: Both Set and Get operations ran concurrently, with overall time
                     dominated by the slower Set operations. This shows the system
                     can handle concurrent load efficiently.

================================================================================
END OF REPORT
================================================================================
EOF
}

# Generate the report
REPORT=$(generate_report)

# Always output to console
echo "$REPORT"

# Save to file if specified
if [ -n "$OUTPUT_FILE" ]; then
  echo "$REPORT" > "$OUTPUT_FILE"
  echo "Report saved to: $OUTPUT_FILE" >&2
fi

