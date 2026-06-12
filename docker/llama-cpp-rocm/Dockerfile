# llama.cpp server for AMD R9700 (RDNA4 / gfx1201), serving Qwen3.6-27B GGUF + MTP.
#
# Why a custom image: upstream ghcr.io/ggml-org/llama.cpp:server-rocm ships ROCm 7.2.1, whose
# rocBLAS/hipBLASLt are NOT gfx1201-tuned -> ~18 tok/s. AMD's gfx120X-tuned ROCm (TheRock,
# 7.13) doubles decode to ~34 tok/s. That tuned stack is only distributed inside AMD's
# rocm/vllm gfx120X image, so we use it as the BUILD base and copy just the runtime libs +
# llama.cpp binaries into a slim final image (the vllm/pytorch payload stays in the build stage).
#
# 2026 best-practices: multi-stage, digest-pinned bases, OCI labels, minimal runtime (no build
# toolchain). The image defaults to a non-root user; the k8s deploy overrides to root for ROCm
# device access (see the llama-server HelmRelease), matching the cluster's other GPU pods.

# ---------------------------------------------------------------------------------------------
# Build stage — AMD's gfx120X-tuned ROCm 7.13 (hipcc + tuned rocBLAS/hipBLASLt + headers).
# Pinned by digest; Renovate keeps the digest current for this tag.
# renovate: datasource=docker depName=docker.io/rocm/vllm
ARG ROCM_BUILD_IMAGE=docker.io/rocm/vllm:rocm7.13.0_gfx120X-all_ubuntu24.04_py3.13_pytorch_2.10.0_vllm_0.19.1@sha256:015dc53ab8c9ddbbdca034c68fe7c169e6884c63094adb49071d1911b1cbd474
FROM ${ROCM_BUILD_IMAGE} AS build

# Single gfx1201 target = smaller, faster build. ROCWMMA flash-attn is slower on gfx12 (#15021),
# keep it OFF. CURL ON so the server can fetch GGUFs via -hf into the model-cache PVC.
ARG GGML_HIP_ROCWMMA_FATTN=OFF
ARG AMDGPU_TARGETS=gfx1201
# Latest llama.cpp release; Renovate bumps this tag.
# renovate: datasource=github-releases depName=ggml-org/llama.cpp
ARG LLAMA_CPP_VERSION=b9611

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends git cmake ninja-build build-essential libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# rocm-sdk wheel layout (TheRock): compiler + headers live under _rocm_sdk_devel, the gfx120X
# Tensile kernels under _rocm_sdk_libraries_gfx120X_all. `rocm-sdk path` resolves the roots.
ENV SP=/opt/python/lib/python3.13/site-packages
ENV ROCM_HOME=${SP}/_rocm_sdk_devel \
    HIP_PATH=${SP}/_rocm_sdk_devel
ENV HIPCXX=${HIP_PATH}/lib/llvm/bin/clang++ \
    PATH=/opt/python/bin:${HIP_PATH}/lib/llvm/bin:${PATH}

WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMA_CPP_VERSION}" https://github.com/ggml-org/llama.cpp . \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS="${AMDGPU_TARGETS}" \
        -DGPU_TARGETS="${AMDGPU_TARGETS}" \
        -DGGML_HIP_ROCWMMA_FATTN="${GGML_HIP_ROCWMMA_FATTN}" \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DCMAKE_PREFIX_PATH="${HIP_PATH}" \
    && cmake --build build --config Release -j"$(nproc)" --target llama-server \
    && mkdir -p /opt/llama/bin /opt/llama/lib \
    && cp build/bin/llama-server /opt/llama/bin/ \
    && cp build/bin/*.so /opt/llama/lib/

# Stage the minimal ROCm runtime the binaries link against (HIP + tuned rocBLAS/hipBLASLt +
# the gfx120X Tensile kernel data). Keeping the /lib subtrees preserves the kernel-data layout.
RUN mkdir -p /opt/rocm-runtime \
    && cp -a ${SP}/_rocm_sdk_core/lib            /opt/rocm-runtime/core \
    && cp -a ${SP}/_rocm_sdk_devel/lib           /opt/rocm-runtime/devel \
    && cp -a ${SP}/_rocm_sdk_libraries_gfx120X_all/lib /opt/rocm-runtime/gfx120X \
    # KEEP llvm/lib — libLLVM/libclang-cpp are a real runtime dep (hipBLASLt -> rocRoller -> LLVM).
    # Drop only the compiler binaries + the cmake dir (not needed at runtime).
    && rm -rf /opt/rocm-runtime/devel/llvm/bin /opt/rocm-runtime/devel/cmake \
    # Fail LOUD if AMD's wheel layout moved (the ${SP} path is tied to the digest-pinned base):
    # a silent miss would otherwise ship a runtime that crashes at first inference.
    && for lib in libamdhip64 librocblas libhipblaslt; do \
         test -n "$(find /opt/rocm-runtime -name "${lib}.so*" -print -quit)" \
           || { echo "FATAL: ${lib} not staged — rocm-sdk layout changed"; exit 1; }; \
       done \
    && test -n "$(find /opt/rocm-runtime/devel/llvm/lib -name 'libLLVM.so*' -print -quit)" \
         || { echo "FATAL: libLLVM not staged — rocRoller/hipBLASLt will fail at runtime"; exit 1; }

# ---------------------------------------------------------------------------------------------
# Runtime stage — slim Ubuntu + only the ROCm runtime libs + llama.cpp binaries. No toolchain.
# renovate: datasource=docker depName=docker.io/library/ubuntu
FROM docker.io/library/ubuntu:24.04@sha256:440dcf6a5640b2ae5c77724e68787a906afb8ddee98bf86db94eea8528c2c076 AS runtime

# Runtime system deps confirmed via ldd of llama-server + libamdhip64/librocblas/libhipblaslt:
# gcc runtime (libstdc++/atomic/gcc_s/gomp), curl (for -hf), numa, ca-certs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 libstdc++6 libatomic1 libgcc-s1 libcurl4 libnuma1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 llama && useradd -u 1000 -g 1000 -m -s /usr/sbin/nologin llama

COPY --from=build /opt/llama/bin/  /app/
COPY --from=build /opt/llama/lib/  /app/lib/
COPY --from=build /opt/rocm-runtime/ /opt/rocm-runtime/

# /dev/kfd + /dev/dri are provided by the pod (squat.ai/dri); groups 'video'(44)/'render'(226)
# are added via the pod securityContext. ROCBLAS/hipBLASLt find their gfx120X kernels via LD path.
ENV LD_LIBRARY_PATH=/app/lib:/opt/rocm-runtime/core:/opt/rocm-runtime/devel:/opt/rocm-runtime/devel/llvm/lib:/opt/rocm-runtime/gfx120X \
    LLAMA_CACHE=/cache \
    HIP_VISIBLE_DEVICES=0

# OCI image annotations (supersede legacy LABEL schema).
LABEL org.opencontainers.image.title="llama-cpp-rocm-gfx1201" \
      org.opencontainers.image.description="llama.cpp server built for AMD RDNA4 gfx1201 (ROCm 7.13 gfx120X-tuned), with MTP" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="MIT"

USER llama
EXPOSE 8080
ENTRYPOINT ["/app/llama-server"]
CMD ["--help"]
