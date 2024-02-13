import pandas as pd
import numpy as np
from pathlib import Path
import pandas
import datetime
from soundbay.utils.logging import Logger
import argparse
from typing import List, Optional


def make_parser():
    parser = argparse.ArgumentParser("Results Analysis")

    parser.add_argument("--num_classes", default=2, help="number of classes for analysis")
    parser.add_argument("--filedir", default="../outputs", help="directory for inference file")
    parser.add_argument("--filename", default="", help="csv file of inference results for analysis")
    parser.add_argument("--selected_class", default="1", help = "selected class, will be annotated raven file")
    parser.add_argument("--save_raven", default=True, help ="whether or not to create a raven file")

    return parser

def analysis_logging(results_df,num_classes):
    """
    Logs the results of the analysis to a wandb.

    Args:
        results_df: The dataframe containing the results of the analysis.
        gt_metadata: The ground truth metadata.

    Returns:
        None

    """
    columns_array = results_df.columns[-int(num_classes):]
    results_array = np.array(results_df[columns_array])
    metrics_dict = Logger.get_metrics_dict(results_df["label"].values.tolist(),
                                       np.argmax(results_array, axis=1).tolist(),
                                       results_array)
    print(metrics_dict)


def inference_csv_to_raven(probs_df: pd.DataFrame, num_classes, seq_len: float, selected_classes: List[str],
                           threshold: float = 0.5, class_names: Optional[List[str]] = None,
                           channel: int = 1, max_freq: float = 20_000) -> pd.DataFrame:
    """ Converts a csv file containing the inference results to a raven csv file.
        Args: probsdataframe: a pandas dataframe containing the inference results.
                      num_classes: the number of classes in the dataset.
                      seq_length: the length of the sequence.
                      selected_classes: the class to be selected.
                      threshold: the threshold to be used for the selection.
                      class_name: the name of the class for which the raven csv file is generated.

        Returns: a pandas dataframe containing the raven csv file.
    """

    if class_names is None:
        class_names = ["class"] * len(selected_classes)

    relevant_columns = probs_df.columns[-int(num_classes):]
    len_dataset = probs_df.shape[0]  # number of segments in wav

    if "begin_time" in probs_df.columns:  # if the dataframe has metadata
        all_begin_times = probs_df["begin_time"].values
    else:  # if the dataframe came from a file with no ground truth
        all_begin_times = np.arange(0, len_dataset * seq_len, seq_len)

    annotations = []

    for selected_class, class_name in zip(selected_classes, class_names):
        if_positive = probs_df[selected_class] > threshold
        begin_times = all_begin_times[if_positive]
        end_times = np.round(begin_times + seq_len, decimals=3)
        if len(end_times) >= 1 and end_times[-1] > round(len_dataset * seq_len, 1):
            end_times[-1] = round(len_dataset * seq_len, 1)  # cut off last bbox if exceeding eof

        # create columns for raven format
        low_freq = np.zeros_like(begin_times)
        high_freq = np.ones_like(begin_times) * max_freq
        view = ['Spectrogram 1'] * len(begin_times)
        selection = np.arange(len(annotations) + 1, len(annotations) + len(begin_times) + 1)
        annotation = [class_name] * len(begin_times)
        channel_array = np.ones_like(begin_times).astype(int) * channel

        annotations.extend(
            list(zip(selection, view, channel_array, begin_times, end_times, low_freq, high_freq, annotation)))

    # Convert annotations list to DataFrame
    annotations_df = pd.DataFrame(annotations,
                                  columns=['Selection', 'View', 'Channel', 'Begin Time (s)', 'End Time (s)',
                                           'Low Freq (Hz)', 'High Freq (Hz)', 'Annotation'])

    return annotations_df

def analysis_main() -> None:
    """
    The main function for running an analysis on a model inference results for required Dataset.

    """
    # configurations:
    args = make_parser().parse_args()
    assert args.filename, "filename argument is empty, you can't run analysis on nothing"
    output_dirpath = Path(args.filedir) #workdir.parent.absolute() / "outputs"
    output_dirpath.mkdir(exist_ok=True)
    save_raven = args.save_raven
    inference_csv_name = args.filename
    inference_results_path = output_dirpath / Path(inference_csv_name + ".csv")
    num_classes = int(args.num_classes)
    threshold = 1/num_classes  # threshold for the classifier in the raven results
    results_df = pd.read_csv(inference_results_path)
    name_col = args.selected_class  # selected class for raven results

    # go through columns and find the one containing the selected class
    column_list = results_df.columns
    selected_class_column = [s for s in column_list if name_col in s][0]
    seq_length_default = 0.2  # relevant only if analyzing an inference single file (not a dataset)
    seq_length = results_df["call_length"].unique()[0] if "call_length" in results_df.columns else seq_length_default  # extract call length from inference results

    if "label" in results_df.columns:
        # log analysis metrics
        analysis_logging(results_df,num_classes)

    # create raven results
    if save_raven:
        thresholdtext = int(threshold*10)
        raven_filename = f"raven_annotations-{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}-{inference_csv_name[37:]}-thresh0{thresholdtext}.csv"
        raven_output_file = output_dirpath / raven_filename

        raven_df = inference_csv_to_raven(results_df, num_classes, seq_length, selected_class= selected_class_column, threshold=threshold, class_name=name_col)
        raven_df.to_csv(index=False, path_or_buf=raven_output_file, sep="\t")


    print("Finished analysis.")


if __name__ == "__main__":
    analysis_main()