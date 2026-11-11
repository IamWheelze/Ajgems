# Brief for a lawyer: data and model licences

Prepared 2026-11-11 for minefoot (satellite detection of visible mining disturbance in Nigeria). It sets out facts from `docs/licence_register.csv` and asks two questions. It is not legal advice.

## What the product does

- **Inputs:** Copernicus Sentinel-2 imagery.
- **Output:** a model scores where mining-related land disturbance is visible, and turns the scores into polygons.
- **Customers:** they receive polygons, a PDF report and the licence notice, but not the model.
- **Training:** the model is trained on labelled examples; the questions below are about two of the things used.

## Question 1: share-alike labels (CC BY-SA 4.0)

**Facts**
- The model is trained on the *Global ML-ready* dataset (Zenodo 14195737, CC BY-SA 4.0). Its masks derive from *Maus et al. v2* polygons (PANGAEA 10.1594/PANGAEA.942325, CC BY-SA 4.0).
- The labels are rasterised and used to fit model weights. They are not redistributed.
- Customers receive our own detections on new imagery, not the dataset.

**We need to know**
1. Are trained model weights an "adapted material" of the CC BY-SA labels, so that sharing the weights would require CC BY-SA?
2. Are our detections on new imagery adapted material?
3. If we never distribute the weights, does share-alike apply at all?
4. What attribution, if any, must outputs and reports carry?
5. Would using these labels only for evaluation, never for training, avoid the question?

**Options we can take**
- Keep using the labels for internal research and evaluation only.
- Retrain on labels we own: the Nigerian sites we are digitising, and examples checked through our own QC pool.
- Accept share-alike on the weights, if the answer is that only the weights are affected and we never distribute them.

## Question 2: pretrained encoder weights

**Facts**
- The model's encoder starts from ResNet-34 ImageNet weights (`smp-hub/resnet34.imagenet`, which copies torchvision's `resnet34-333f7ec4.pth`).
- The Hugging Face card says "license: other" and gives no terms.
- Torchvision says pretrained models "may have their own licenses or terms and conditions derived from the dataset used for training. It is your responsibility to determine whether you have permission to use the models for your use case."
- ImageNet's terms allow non-commercial research and educational use.
- We fine-tune the whole network on satellite data, and do not distribute it.

**We need to know**
1. May a commercial service use a model initialised from these weights?
2. Does fine-tuning or non-distribution change the answer?

**Options we can take**
- Retrain from random initialisation (`minefoot g2 --encoder-weights none`). This removes the question, possibly at some cost in accuracy, which we would measure with the paired comparison.
- Use encoder weights with an explicit commercial licence, for example weights pretrained on satellite imagery under a permissive licence.

## Not in question

- **Copernicus Sentinel data:** free, full and open, including commercially, with the credit "Contains modified Copernicus Sentinel data [year]". Our exports carry it.
- **Microsoft Planetary Computer:** its service terms limit it to evaluation and testing. The app already refuses it for commercial projects.
