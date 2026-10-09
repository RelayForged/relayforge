# Relay Benchmark Suite

Production-grade experimental evaluation framework comparing the **Adaptive Event Delivery Engine (AEDE)** against static and adaptive retry baselines across 11 failure injection scenarios.

## Detailed Walkthrough & Architecture
For an in-depth explanation of what was done, the research context, the mathematical models, and the Phase 2 improvements (Bursty Poisson generation, time-decay ERI windows, and Genetic Algorithm optimization), see:
👉 **[`benchmark/WALKTHROUGH.md`](file:///c:/Users/sammj/MajorProject/benchmark/WALKTHROUGH.md)**

## Quick Start

### 1. Run Complete Benchmark Matrix (Strategy A vs B vs C)
```bash
python benchmark/run_experiments.py
```

### 2. Run Automated Genetic ERI Weight Optimizer (DEAP)
```bash
python benchmark/genetic_optimizer.py --generations 10 --population 20 --verbose
```

### 3. Run Weight Sensitivity & Robustness Analysis
```bash
python benchmark/sensitivity_analysis.py
```

### 4. Run Multi-Endpoint Contention Simulation
```bash
python benchmark/multi_endpoint.py
```

### 5. Run Benchmark Unit Tests
```bash
pytest tests/unit/test_benchmark.py tests/unit/test_genetic_optimizer.py -v
```
